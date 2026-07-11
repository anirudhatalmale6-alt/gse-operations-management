"""Dashboard aggregation and the readiness calculation."""

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from .models import Equipment, Location, Movement, ReadinessSnapshot


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
