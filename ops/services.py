"""Dashboard aggregation and the readiness calculation."""

from collections import Counter, defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from .models import (
    Equipment,
    Location,
    Movement,
    ReadinessSnapshot,
    ServiceEvent,
)


def _expiry_q(today, horizon=None):
    """Q object matching equipment with any compliance date expired / expiring."""
    fields = ["pm_due_date", "tuv_expiry_date", "ramp_pass_expiry_date",
              "insurance_expiry_date"]
    q = Q()
    for f in fields:
        if horizon is None:
            q |= Q(**{f"{f}__lt": today})
        else:
            q |= Q(**{f"{f}__gte": today, f"{f}__lte": horizon})
    return q


def dashboard_stats(queryset=None, today=None):
    """Every headline number the dashboard shows, from one pass over the data."""
    today = today or timezone.localdate()
    horizon = today + timedelta(days=settings.EXPIRY_WARNING_DAYS)
    qs = Equipment.objects.all() if queryset is None else queryset

    # order_by() is load-bearing: Equipment.Meta.ordering would otherwise be folded
    # into the GROUP BY and give one row per (status, equipment_number).
    by_status = dict(
        qs.order_by()
        .values_list("status")
        .annotate(n=Count("id"))
        .values_list("status", "n")
    )
    S = Equipment.Status

    total = sum(by_status.values())
    scrap = by_status.get(S.SCRAP, 0)
    # Scrapped units are off the books -- they must not drag readiness down forever.
    fleet = total - scrap
    serviceable = by_status.get(S.AVAILABLE, 0) + by_status.get(S.ALLOCATED, 0)
    readiness = round(serviceable / fleet * 100, 1) if fleet else 0.0

    expired = qs.filter(_expiry_q(today)).exclude(status=S.SCRAP).distinct().count()
    upcoming = (
        qs.filter(_expiry_q(today, horizon))
        .exclude(_expiry_q(today))
        .exclude(status=S.SCRAP)
        .distinct()
        .count()
    )

    return {
        "total": total,
        "fleet": fleet,
        "available": by_status.get(S.AVAILABLE, 0),
        "allocated": by_status.get(S.ALLOCATED, 0),
        "maintenance": by_status.get(S.MAINTENANCE, 0),
        "pm_due": by_status.get(S.PM_DUE, 0),
        "tuv_expired": by_status.get(S.TUV_EXPIRED, 0),
        "ramp_pass_expired": by_status.get(S.RAMP_PASS_EXPIRED, 0),
        "oos": by_status.get(S.OOS, 0),
        "scrap": scrap,
        "expired": expired,
        "upcoming": upcoming,
        "readiness": readiness,
        "warning_days": settings.EXPIRY_WARNING_DAYS,
    }


def location_breakdown(queryset=None):
    """Per-location availability table for the dashboard."""
    qs = Equipment.objects.all() if queryset is None else queryset
    S = Equipment.Status
    rows = (
        Location.objects.filter(active=True)
        .annotate(
            total=Count("equipment_here", filter=Q(equipment_here__in=qs)),
            available=Count(
                "equipment_here",
                filter=Q(equipment_here__in=qs, equipment_here__status=S.AVAILABLE),
            ),
            allocated=Count(
                "equipment_here",
                filter=Q(equipment_here__in=qs, equipment_here__status=S.ALLOCATED),
            ),
            down=Count(
                "equipment_here",
                filter=Q(
                    equipment_here__in=qs,
                    equipment_here__status__in=[
                        S.MAINTENANCE, S.PM_DUE, S.TUV_EXPIRED,
                        S.RAMP_PASS_EXPIRED, S.OOS,
                    ],
                ),
            ),
        )
        .order_by("station", "name")
    )
    out = []
    for loc in rows:
        active_fleet = loc.available + loc.allocated + loc.down
        loc.readiness = (
            round((loc.available + loc.allocated) / active_fleet * 100, 1)
            if active_fleet
            else 0.0
        )
        if loc.total:
            out.append(loc)
    return out


def refresh_compliance_statuses(today=None):
    """Re-evaluate every unit's status against its compliance dates.

    Run nightly (management command `refresh_compliance`) so a TUV that lapses at
    midnight shows as TUV Expired the next morning without anyone touching it.
    """
    changed = 0
    for eq in Equipment.objects.exclude(status__in=Equipment.MANUAL_ONLY):
        if eq.apply_compliance_status(today=today):
            changed += 1
    return changed


def take_readiness_snapshot(today=None):
    """Store today's readiness, fleet-wide and per location, for trending."""
    today = today or timezone.localdate()
    stats = dashboard_stats(today=today)
    ReadinessSnapshot.objects.update_or_create(
        snapshot_date=today,
        location=None,
        defaults={
            "total": stats["fleet"],
            "serviceable": stats["available"] + stats["allocated"],
            "readiness_pct": stats["readiness"],
        },
    )
    for loc in location_breakdown():
        ReadinessSnapshot.objects.update_or_create(
            snapshot_date=today,
            location=loc,
            defaults={
                "total": loc.total,
                "serviceable": loc.available + loc.allocated,
                "readiness_pct": loc.readiness,
            },
        )


def readiness_trend(days=14):
    """(labels, values) for the dashboard trend chart."""
    since = timezone.localdate() - timedelta(days=days)
    rows = (
        ReadinessSnapshot.objects.filter(location__isnull=True, snapshot_date__gte=since)
        .order_by("snapshot_date")
        .values_list("snapshot_date", "readiness_pct")
    )
    return (
        [d.strftime("%d %b") for d, _ in rows],
        [float(v) for _, v in rows],
    )


def pending_approvals():
    return (
        Movement.objects.filter(status=Movement.Status.PENDING)
        .select_related("equipment", "from_location", "to_location", "allocated_by")
    )


# ------------------------------------------------------------- OOS / daily report


def oos_register(as_of=None):
    """Every unit currently out of operation, oldest first, with real ageing.

    The workbook computes this with =DAYS(NOW(), start) and every row of the
    live file renders #NAME?, so the ageing is invisible today. This is that
    column, working, and it is what surfaces the units that have been sitting
    in the workshop for years.
    """
    as_of = as_of or timezone.localdate()
    events = (
        ServiceEvent.objects.filter(closed_on__isnull=True)
        .select_related("equipment", "equipment__equipment_type")
        .order_by("start_date")
    )
    rows = []
    for e in events:
        rows.append({"event": e, "days": e.days_out(as_of), "bucket": e.age_bucket})
    return rows


def oos_aging(as_of=None):
    """Bucketed ageing of the open OOS list, plus the worst offenders."""
    rows = oos_register(as_of)
    order = ["0-7", "8-30", "31-60", "61-90", "90+"]
    buckets = {k: 0 for k in order}
    for r in rows:
        buckets[r["bucket"]] += 1
    total = len(rows) or 1
    stale = [r for r in rows if r["days"] > 90]
    stale.sort(key=lambda r: -r["days"])
    by_reason = Counter(r["event"].get_reason_display() for r in rows)
    days = sorted(r["days"] for r in rows)
    return {
        "rows": rows,
        "total": len(rows),
        "buckets": [
            {"label": k, "n": buckets[k], "pct": round(buckets[k] / total * 100)}
            for k in order
        ],
        "by_reason": dict(by_reason),
        "stale": stale,
        "stale_count": len(stale),
        "avg_days": round(sum(days) / len(days)) if days else 0,
        "median_days": days[len(days) // 2] if days else 0,
        "max_days": days[-1] if days else 0,
        # What in-service % would be if the long-dead units were written off.
        "recoverable_pct": round(len(stale) / total * 100) if days else 0,
    }


def daily_report(as_of=None):
    """Rebuild the station's DAILY GSE REPORT from the database.

    Same shape as the spreadsheet, same arithmetic (G = C - D + E + F), but every
    number is counted rather than typed, so the report and the OOS list cannot
    drift apart.
    """
    as_of = as_of or timezone.localdate()
    open_events = ServiceEvent.objects.filter(closed_on__isnull=True)

    # OOS counts per (type, reason) in one query.
    oos_by_type = defaultdict(lambda: defaultdict(int))
    for tname, reason in open_events.values_list(
        "equipment__equipment_type__name", "reason"
    ):
        oos_by_type[tname][ServiceEvent.REASON_TO_COLUMN[reason]] += 1

    counts = (
        Equipment.objects.order_by()
        .values("equipment_type__name")
        .annotate(
            total=Count("id"),
            overage=Count("id", filter=Q(is_overage=True)),
            support_in=Count(
                "id", filter=Q(ownership=Equipment.Ownership.LOCAL_SUPPORT)
            ),
            support_out=Count(
                "id", filter=Q(ownership=Equipment.Ownership.OUT_STATION)
            ),
        )
        .order_by("equipment_type__name")
    )

    lines, totals = [], defaultdict(int)
    for c in counts:
        name = c["equipment_type__name"]
        o = oos_by_type.get(name, {})
        rs, tuv, pm, rep = o.get("R/S", 0), o.get("TUV", 0), o.get("PM", 0), o.get(
            "REPAIR", 0
        )
        oos = rs + tuv + pm + rep
        actual = c["total"]
        in_svc = actual - oos
        line = {
            "type": name,
            "grand_total": actual - c["support_in"] + c["support_out"] - c["overage"],
            "to_out_station": c["support_out"],
            "overage": c["overage"],
            "from_local": c["support_in"],
            "actual": actual,
            "rs": rs,
            "tuv": tuv,
            "pm": pm,
            "repair": rep,
            "oos": oos,
            "in_service": in_svc,
            "oos_pct": round(oos / actual * 100, 1) if actual else 0,
            "in_service_pct": round(in_svc / actual * 100, 1) if actual else 0,
        }
        lines.append(line)
        for k in ("grand_total", "to_out_station", "overage", "from_local", "actual",
                  "rs", "tuv", "pm", "repair", "oos", "in_service"):
            totals[k] += line[k]

    actual = totals["actual"] or 1
    totals["oos_pct"] = round(totals["oos"] / actual * 100, 1)
    totals["in_service_pct"] = round(totals["in_service"] / actual * 100, 1)

    return {"as_of": as_of, "lines": lines, "totals": dict(totals)}


def location_matrix():
    """In-service units per parking location x equipment type -- the second grid
    on the daily sheet. Only serviceable units are counted, as on the sheet."""
    grid = defaultdict(lambda: defaultdict(int))
    types = set()
    qs = (
        Equipment.objects.filter(location__isnull=False)
        .exclude(service_events__closed_on__isnull=True)
        .order_by()
        .values("location__name", "equipment_type__name")
        .annotate(n=Count("id"))
    )
    for r in qs:
        grid[r["location__name"]][r["equipment_type__name"]] += r["n"]
        types.add(r["equipment_type__name"])
    types = sorted(types)
    rows = []
    for loc in sorted(grid):
        cells = [grid[loc].get(t, 0) for t in types]
        rows.append({"location": loc, "cells": cells, "total": sum(cells)})
    return {"types": types, "rows": rows}
