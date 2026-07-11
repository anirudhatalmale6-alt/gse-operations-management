"""Import the station's live GSE workbook (TH.xlsx) into the database.

Reads three sheets:
  * "Total Equipment Count Jeddah"  -> the equipment master (one row per unit)
  * "GSE OUT OF OPERATION (OOS)"    -> the open out-of-service log
  * "DAILY GSE REPORT"              -> the parking locations and the daily trend

The importer is idempotent: run it again on a newer workbook and it updates the
units it already knows and adds the ones it does not.
"""

import datetime
import re
import warnings

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ops.models import (
    Department,
    Equipment,
    EquipmentType,
    Location,
    Movement,
    ReadinessSnapshot,
    ServiceEvent,
)

# The master writes some types differently from the OOS sheet. One name each.
TYPE_ALIASES = {
    "towbar": "Tow Bar",
    "tow bar": "Tow Bar",
    "air brake cooler": "Aircraft Brake Cooler",
    "aircraft brake cooler": "Aircraft Brake Cooler",
    "golfcart": "Golf Cart",
    "golf cart": "Golf Cart",
    "highloader": "High Loader",
    "high loader": "High Loader",
    "aircondition unit": "ACU",
    "air starter unit": "ASU",
    "ground power unit": "GPU",
}

TYPE_CODES = {
    "Baggage Tractor": "BGT",
    "Tow Bar": "TWB",
    "Conveyor Belt": "CVB",
    "Passenger Step": "PXS",
    "High Loader": "HLD",
    "Pushback": "PBK",
    "Conventional Pushback": "PBC",
    "Towbarless Pushback": "PBT",
    "ACU": "ACU",
    "GPU": "GPU",
    "Transporter": "TRP",
    "Medical Lift": "MDL",
    "ASU": "ASU",
    "Aircraft Brake Cooler": "ABC",
    "Cool Dolly": "CDY",
    "Forklift": "FKL",
    "Golf Cart": "GLF",
}

UNPOWERED = {"Tow Bar", "Aircraft Brake Cooler", "Cool Dolly"}

REASON_MAP = {
    "repair": ServiceEvent.Reason.REPAIR,
    "pm": ServiceEvent.Reason.PM,
    "tuv": ServiceEvent.Reason.TUV,
    "r/s": ServiceEvent.Reason.RS,
    "rs": ServiceEvent.Reason.RS,
}

# Reason -> the equipment status it forces while the unit is out.
REASON_STATUS = {
    ServiceEvent.Reason.REPAIR: Equipment.Status.MAINTENANCE,
    ServiceEvent.Reason.PM: Equipment.Status.PM_DUE,
    ServiceEvent.Reason.TUV: Equipment.Status.TUV_EXPIRED,
    ServiceEvent.Reason.RS: Equipment.Status.OOS,
}


def norm_type(raw):
    if not raw:
        return None
    name = re.sub(r"\s+", " ", str(raw)).strip()
    return TYPE_ALIASES.get(name.lower(), name.title() if name.islower() else name)


def as_date(v):
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return None


def clean(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


class Command(BaseCommand):
    help = "Import the station GSE workbook into the database."

    def add_arguments(self, parser):
        parser.add_argument("xlsx", help="Path to the workbook, e.g. TH.xlsx")
        parser.add_argument(
            "--station", default="JED", help="IATA station code (default JED)"
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing equipment/service events first.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        try:
            import openpyxl
        except ImportError as exc:  # pragma: no cover
            raise CommandError("openpyxl is required: pip install openpyxl") from exc

        warnings.filterwarnings("ignore")
        wb = openpyxl.load_workbook(opts["xlsx"], data_only=True)
        station = opts["station"]

        if opts["flush"]:
            # Order matters: children first, then the masters they protect.
            ServiceEvent.objects.all().delete()
            Movement.objects.all().delete()
            Equipment.objects.all().delete()
            ReadinessSnapshot.objects.all().delete()
            EquipmentType.objects.all().delete()
            Location.objects.all().delete()

        dept, _ = Department.objects.get_or_create(
            code="GSE", defaults={"name": "GSE Maintenance"}
        )
        ramp_dept, _ = Department.objects.get_or_create(
            code="RAMP", defaults={"name": "Ramp Operations"}
        )

        locations = self._import_locations(wb, station, ramp_dept)
        types = self._import_types()
        made, updated = self._import_equipment(wb, types, dept)
        events, refined, missing = self._import_oos(wb, station, ramp_dept)
        self._import_trend(wb)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Import complete"))
        self.stdout.write(f"  locations      : {len(locations)}")
        self.stdout.write(f"  equipment types: {len(types)}")
        self.stdout.write(f"  equipment      : {made} created, {updated} updated")
        self.stdout.write(f"  open OOS events: {events}")
        self.stdout.write(f"  pushbacks typed from the OOS sheet: {refined}")
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"  OOS rows whose GS number is NOT in the master: {len(missing)}"
                )
            )
            for gs in missing:
                self.stdout.write(f"      {gs}")

        unresolved = Equipment.objects.filter(equipment_type__name="Pushback").count()
        if unresolved:
            self.stdout.write(
                self.style.WARNING(
                    f"  pushbacks still unclassified (conventional vs towbarless): "
                    f"{unresolved}"
                )
            )

    # -- locations ----------------------------------------------------------

    def _import_locations(self, wb, station, dept):
        """Parking locations come from the 'in operation per location' block."""
        ws = wb["DAILY GSE REPORT"]
        made = {}
        for row in range(24, 44):
            name = clean(ws.cell(row=row, column=2).value)
            if not name or name.lower().startswith("total"):
                continue
            name = name.replace("\xa0", " ").replace("----------", "Station").strip()
            area = "New Airport" if name.lower().startswith("new airport") else ""
            if name.lower().startswith(("ramp", "apron")):
                area = "Airside"
            loc, _ = Location.objects.get_or_create(
                station=station,
                name=name[:100],
                defaults={"area": area, "department": dept},
            )
            made[name] = loc
        # The OOS sheet parks units in a workshop, which is not a stand.
        for extra in ["Workshop", "Gate 3"]:
            loc, _ = Location.objects.get_or_create(
                station=station,
                name=extra,
                defaults={"area": "Maintenance", "department": dept},
            )
            made[extra] = loc
        return made

    def _import_types(self):
        types = {}
        for name, code in TYPE_CODES.items():
            t, _ = EquipmentType.objects.get_or_create(
                name=name,
                defaults={"code": code, "powered": name not in UNPOWERED},
            )
            types[name] = t
        return types

    # -- equipment ----------------------------------------------------------

    def _import_equipment(self, wb, types, dept):
        ws = wb["Total Equipment Count Jeddah"]
        made = updated = 0
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=7):
            gs = clean(row[1].value)
            if not gs:
                continue
            fleet = clean(row[2].value) or None
            tname = norm_type(row[3].value)
            remarks = clean(row[4].value)
            note = clean(row[5].value)

            t = types.get(tname)
            if t is None:
                t, _ = EquipmentType.objects.get_or_create(
                    name=tname, defaults={"code": tname[:3].upper()}
                )
                types[tname] = t

            low = remarks.lower()
            if "sra" in low:
                ownership = Equipment.Ownership.SRA
            elif "from local station" in low:
                ownership = Equipment.Ownership.LOCAL_SUPPORT
            else:
                ownership = Equipment.Ownership.STATION

            # The note column carries the pushback sub-type when someone filled it in.
            if note.lower() == "towbarless":
                t = types["Towbarless Pushback"]
            elif note.lower() == "conventional":
                t = types["Conventional Pushback"]

            obj, created = Equipment.objects.update_or_create(
                equipment_number=gs,
                defaults={
                    "fleet_number": fleet,
                    "equipment_type": t,
                    "department": dept,
                    "ownership": ownership,
                    "is_overage": "overage" in note.lower(),
                    "remarks": note,
                    "status": Equipment.Status.AVAILABLE,
                },
            )
            made += created
            updated += not created
        return made, updated

    # -- out of operation ---------------------------------------------------

    def _import_oos(self, wb, station, dept):
        ws = wb["GSE OUT OF OPERATION (OOS)"]
        types = {t.name: t for t in EquipmentType.objects.all()}
        count = refined = 0
        missing = []
        for row in ws.iter_rows(min_row=12, max_row=ws.max_row, max_col=18):
            v = [c.value for c in row]
            gs = clean(v[5])
            if not gs:
                continue
            try:
                eq = Equipment.objects.get(equipment_number=gs)
            except Equipment.DoesNotExist:
                missing.append(gs)
                continue

            # The OOS sheet knows conventional from towbarless; the master does not.
            tname = norm_type(v[7])
            if tname in ("Conventional Pushback", "Towbarless Pushback"):
                if eq.equipment_type.name != tname:
                    eq.equipment_type = types[tname]
                    refined += 1

            start = as_date(v[8])
            if not start:
                continue
            reason = REASON_MAP.get(clean(v[12]).lower())
            if reason is None:
                continue

            loc_name = clean(v[14]).replace("----------", "Station")
            if loc_name:
                loc, _ = Location.objects.get_or_create(
                    station=station,
                    name=loc_name[:100],
                    defaults={"area": "Maintenance", "department": dept},
                )
                eq.location = loc

            eq.status = REASON_STATUS[reason]
            eq.save()

            ServiceEvent.objects.update_or_create(
                equipment=eq,
                start_date=start,
                closed_on=None,
                defaults={
                    "section": clean(v[1]) or "Ramp",
                    "reason": reason,
                    "service_request_number": clean(v[11]),
                    "workshop_location": loc_name,
                    "status_note": clean(v[13]),
                    "work_order_request_time": clean(v[15]),
                    "time_sent_to_workshop": clean(v[16]),
                    "gse_group_remarks": clean(v[17]),
                },
            )
            count += 1
        return count, refined, missing

    # -- daily trend --------------------------------------------------------

    def _import_trend(self, wb):
        """The month-to-date in-service percentages at the foot of the report."""
        ws = wb["DAILY GSE REPORT"]
        for row in range(69, 100):
            day = as_date(ws.cell(row=row, column=2).value)
            pct = ws.cell(row=row, column=3).value
            if not day or not isinstance(pct, (int, float)):
                continue
            ReadinessSnapshot.objects.update_or_create(
                snapshot_date=day,
                location=None,
                defaults={"readiness_pct": round(float(pct) * 100, 1)},
            )
