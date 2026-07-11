"""Load a realistic demo fleet so the client can click around a populated system."""

import random
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ops.models import (
    Department,
    Employee,
    Equipment,
    EquipmentType,
    Location,
    Movement,
    ReadinessSnapshot,
    TrainingCourse,
    TrainingRecord,
)

TYPES = [
    ("BLT", "Belt Loader", True),
    ("PBT", "Pushback Tractor", True),
    ("GPU", "Ground Power Unit", True),
    ("ACU", "Air Conditioning Unit", True),
    ("ASU", "Air Start Unit", True),
    ("ULD", "ULD / Container Loader", True),
    ("PSU", "Passenger Stairs", True),
    ("WTR", "Water Truck", True),
    ("LAV", "Lavatory Truck", True),
    ("BGT", "Baggage Tractor", True),
    ("DLY", "Baggage Dolly", False),
    ("CHK", "Wheel Chocks Set", False),
]

DEPARTMENTS = [
    ("RMP", "Ramp Operations"),
    ("BAG", "Baggage Handling"),
    ("CGO", "Cargo"),
    ("MNT", "GSE Maintenance"),
    ("CBN", "Cabin Services"),
]

LOCATIONS = [
    ("Terminal 1 Apron", "DXB", "Apron", "Zone A", "RMP"),
    ("Terminal 3 Apron", "DXB", "Apron", "Zone C", "RMP"),
    ("Concourse B Stands", "DXB", "Concourse", "Zone B", "RMP"),
    ("Baggage Hall South", "DXB", "Terminal", "Zone S", "BAG"),
    ("Cargo Mega Terminal", "DXB", "Cargo City", "Zone K", "CGO"),
    ("GSE Workshop", "DXB", "Maintenance", "Zone M", "MNT"),
    ("Remote Stand 200s", "DXB", "Remote", "Zone R", "RMP"),
    ("Sharjah Ramp", "SHJ", "Apron", "Zone A", "RMP"),
]

NAMES = [
    "Rashid Al Marri", "Sanjay Menon", "Fahad Al Suwaidi", "Maria Santos",
    "Ibrahim Khan", "Joseph Fernandes", "Ahmed Nasser", "Ravi Pillai",
    "Daniel Okoye", "Sunil Verma", "Omar Haddad", "Elena Petrova",
    "Arun Kumar", "Yousef Al Ali", "Grace Wanjiku", "Peter Cheng",
]

COURSES = [
    ("GSE-101", "GSE Driving Permit", 24),
    ("RMP-201", "Airside Ramp Safety", 12),
    ("PBT-301", "Pushback & Towing", 24),
    ("DGR-401", "Dangerous Goods Awareness", 24),
    ("HLD-501", "Loader Operation (ULD)", 36),
]

# Plausible make/model per equipment type -- a GSE engineer will read these.
TYPE_MODELS = {
    "BLT": [("TLD", "NBL-60"), ("Mulag", "Comet 4"), ("Power Stow", "Rollertrack")],
    "PBT": [("TLD", "TPX-200"), ("Goldhofer", "AST-1X"), ("Kalmar", "TBL-180")],
    "GPU": [("TLD", "GPU-4090"), ("ITW GSE", "7400"), ("Guinault", "GA-90")],
    "ACU": [("TLD", "ACU-804"), ("Guinault", "ACU-302"), ("ITW GSE", "AHU-3000")],
    "ASU": [("TLD", "ASU-600"), ("Guinault", "ASE-120"), ("ITW GSE", "AS-160")],
    "ULD": [("TLD", "TXL-838"), ("JBT", "Commander 15"), ("Trepel", "Champ 150")],
    "PSU": [("Aviogei", "PS-180"), ("TLD", "PS-717"), ("Mallaghan", "PSU-2000")],
    "WTR": [("Mallaghan", "WT-2000"), ("Aviogei", "WS-400"), ("TLD", "WS-100")],
    "LAV": [("Mallaghan", "LT-1200"), ("Aviogei", "LS-400"), ("TLD", "LS-100")],
    "BGT": [("Charlatte", "TE-206"), ("Mulag", "Comet 3"), ("Kalmar", "TT-618")],
    "DLY": [("Nordisk", "LD-3 Dolly"), ("Alvest", "DPL-1500"), ("Trepel", "D-450")],
    "CHK": [("Aerospecialties", "AC-1000"), ("Checkers", "AC-Series")],
}


class Command(BaseCommand):
    help = "Seed a demo GSE fleet (idempotent-ish: wipes ops data first)."

    def add_arguments(self, parser):
        parser.add_argument("--equipment", type=int, default=140)

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(7)  # deterministic demo data
        today = timezone.localdate()

        self.stdout.write("Clearing existing demo data...")
        Movement.objects.all().delete()
        ReadinessSnapshot.objects.all().delete()
        TrainingRecord.objects.all().delete()
        Equipment.objects.all().delete()
        Location.objects.all().delete()
        Employee.objects.all().delete()
        TrainingCourse.objects.all().delete()
        EquipmentType.objects.all().delete()
        Department.objects.all().delete()

        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "is_staff": True,
                "is_superuser": True,
                "first_name": "Ops",
                "last_name": "Admin",
            },
        )
        if created:
            admin.set_password("gse2026")
            admin.save()

        supervisor, created = User.objects.get_or_create(
            username="supervisor",
            defaults={"is_staff": False, "first_name": "Ramp", "last_name": "Supervisor"},
        )
        if created:
            supervisor.set_password("gse2026")
            supervisor.save()

        depts = {
            code: Department.objects.create(code=code, name=name)
            for code, name in DEPARTMENTS
        }
        types = [
            EquipmentType.objects.create(code=c, name=n, powered=p)
            for c, n, p in TYPES
        ]
        locations = [
            Location.objects.create(
                name=name, station=st, area=area, zone=zone, department=depts[d]
            )
            for name, st, area, zone, d in LOCATIONS
        ]
        workshop = next(l for l in locations if l.name == "GSE Workshop")
        ramp_locations = [l for l in locations if l != workshop]

        employees = []
        for i, name in enumerate(NAMES):
            dept = depts[rng.choice(list(depts))]
            employees.append(
                Employee.objects.create(
                    employee_number=f"EMP{1001 + i}",
                    full_name=name,
                    designation=rng.choice(
                        ["GSE Operator", "Ramp Agent", "Supervisor",
                         "GSE Technician", "Team Leader"]
                    ),
                    department=dept,
                    base_location=rng.choice(locations),
                    phone=f"+9715{rng.randint(10000000, 59999999)}",
                    joined_on=today - timedelta(days=rng.randint(200, 3000)),
                )
            )
        for loc in locations:
            loc.supervisor = rng.choice(employees)
            loc.save(update_fields=["supervisor"])

        courses = [
            TrainingCourse.objects.create(code=c, name=n, validity_months=v)
            for c, n, v in COURSES
        ]
        for emp in employees:
            for course in rng.sample(courses, rng.randint(2, 4)):
                completed = today - timedelta(days=rng.randint(30, 900))
                TrainingRecord.objects.create(
                    employee=emp, course=course, completed_on=completed
                )

        # ---- fleet -------------------------------------------------------
        n = options["equipment"]
        counters = {t.code: 0 for t in types}
        equipment = []
        for _ in range(n):
            t = rng.choice(types)
            counters[t.code] += 1
            mfr, model = rng.choice(TYPE_MODELS[t.code])
            loc = rng.choice(ramp_locations)

            def d(lo, hi):
                return today + timedelta(days=rng.randint(lo, hi))

            # Most of the fleet is healthy; a realistic minority is lapsing.
            roll = rng.random()
            if roll < 0.06:
                pm, tuv, ramp = d(-60, -2), d(30, 400), d(30, 400)
            elif roll < 0.10:
                pm, tuv, ramp = d(20, 300), d(-90, -3), d(30, 400)
            elif roll < 0.13:
                pm, tuv, ramp = d(20, 300), d(30, 400), d(-45, -1)
            elif roll < 0.28:
                pm, tuv, ramp = d(2, 28), d(5, 29), d(60, 500)
            else:
                pm, tuv, ramp = d(45, 300), d(60, 700), d(60, 700)

            eq = Equipment.objects.create(
                equipment_number=f"{t.code}-{counters[t.code]:03d}",
                equipment_type=t,
                model=model,
                manufacturer=mfr,
                serial_number=f"{mfr[:2].upper()}{rng.randint(100000, 999999)}",
                registration_number=(
                    f"GSE-{rng.randint(1000, 9999)}" if t.powered else ""
                ),
                purchase_date=today - timedelta(days=rng.randint(300, 4000)),
                location=loc,
                department=loc.department,
                status=Equipment.Status.AVAILABLE,
                pm_due_date=pm if t.powered else None,
                tuv_expiry_date=tuv if t.powered else None,
                ramp_pass_expiry_date=ramp if t.powered else None,
                insurance_expiry_date=d(30, 500) if t.powered else None,
            )
            equipment.append(eq)

        # A handful in the workshop / OOS / scrapped -- set by hand, as a supervisor would.
        for eq in rng.sample(equipment, 10):
            eq.status = Equipment.Status.MAINTENANCE
            eq.location = workshop
            eq.remarks = rng.choice([
                "Hydraulic leak on lift cylinder. Awaiting seal kit.",
                "Engine overheating - radiator flush in progress.",
                "Scheduled 500-hour preventive maintenance.",
                "Brake pads replacement.",
            ])
            eq.save()
        for eq in rng.sample([e for e in equipment if e.status == Equipment.Status.AVAILABLE], 5):
            eq.status = Equipment.Status.OOS
            eq.remarks = "Accident damage - awaiting insurance assessment."
            eq.save()
        for eq in rng.sample([e for e in equipment if e.status == Equipment.Status.AVAILABLE], 3):
            eq.status = Equipment.Status.SCRAP
            eq.remarks = "Beyond economical repair. Disposed."
            eq.save()

        # Compliance engine decides PM Due / TUV Expired / Ramp Pass Expired.
        for eq in equipment:
            eq.apply_compliance_status()

        # ---- movement history --------------------------------------------
        movable = [e for e in equipment if e.status == Equipment.Status.AVAILABLE]
        for eq in rng.sample(movable, min(45, len(movable))):
            target = rng.choice([l for l in ramp_locations if l != eq.location])
            days_ago = rng.randint(0, 12)
            mv = Movement.objects.create(
                equipment=eq,
                movement_type=Movement.Type.ALLOCATE,
                from_location=eq.location,
                to_location=target,
                allocation_date=today - timedelta(days=days_ago),
                allocation_time=time(rng.randint(5, 21), rng.choice([0, 15, 30, 45])),
                allocated_by=supervisor,
                reason=rng.choice([
                    "Turnaround support - EK521 Bay 32",
                    "Peak-hour bank coverage",
                    "Cargo build-up support",
                    "Replacement for unit under maintenance",
                    "Night-shift towing operations",
                ]),
            )
            mv.approve(admin)

        # A few requests still waiting for a supervisor's decision.
        for eq in rng.sample(
            [e for e in equipment if e.status == Equipment.Status.AVAILABLE], 6
        ):
            target = rng.choice([l for l in ramp_locations if l != eq.location])
            Movement.objects.create(
                equipment=eq,
                movement_type=rng.choice(
                    [Movement.Type.ALLOCATE, Movement.Type.TRANSFER]
                ),
                from_location=eq.location,
                to_location=target,
                allocation_date=today,
                allocation_time=time(rng.randint(6, 20), 0),
                allocated_by=supervisor,
                reason=rng.choice([
                    "Additional GPU for A380 stand",
                    "Shift handover redistribution",
                    "Cover for unit sent to workshop",
                ]),
            )

        # ---- 14 days of readiness history for the trend chart -------------
        fleet = Equipment.objects.exclude(status=Equipment.Status.SCRAP).count()
        for i in range(14, 0, -1):
            day = today - timedelta(days=i)
            pct = round(rng.uniform(86.0, 96.0), 1)
            ReadinessSnapshot.objects.create(
                snapshot_date=day,
                location=None,
                total=fleet,
                serviceable=int(fleet * pct / 100),
                readiness_pct=Decimal(str(pct)),
            )
        from ops.services import take_readiness_snapshot

        take_readiness_snapshot()

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(equipment)} units, {len(locations)} locations, "
                f"{len(employees)} employees, {Movement.objects.count()} movements.\n"
                "Logins: admin / gse2026  and  supervisor / gse2026"
            )
        )
