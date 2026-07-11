from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from ops import services
from ops.forms import MovementForm
from ops.models import (
    Department,
    Equipment,
    EquipmentType,
    Location,
    Movement,
    ServiceEvent,
)


class Base(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.user = User.objects.create_user("sup", password="x")
        self.dept = Department.objects.create(code="RMP", name="Ramp")
        self.type = EquipmentType.objects.create(code="BLT", name="Belt Loader")
        self.apron = Location.objects.create(
            name="Apron A", station="DXB", department=self.dept
        )
        self.workshop = Location.objects.create(
            name="Workshop", station="DXB", department=self.dept
        )

    def make(self, number="BLT-001", **kwargs):
        defaults = dict(
            equipment_number=number,
            equipment_type=self.type,
            location=self.apron,
            department=self.dept,
            status=Equipment.Status.AVAILABLE,
        )
        defaults.update(kwargs)
        return Equipment.objects.create(**defaults)


class ComplianceEngineTests(Base):
    def test_lapsed_tuv_grounds_the_unit(self):
        eq = self.make(tuv_expiry_date=self.today - timedelta(days=1))
        eq.apply_compliance_status()
        self.assertEqual(eq.status, Equipment.Status.TUV_EXPIRED)

    def test_renewing_the_certificate_returns_the_unit_to_available(self):
        eq = self.make(tuv_expiry_date=self.today - timedelta(days=1))
        eq.apply_compliance_status()
        eq.tuv_expiry_date = self.today + timedelta(days=365)
        eq.apply_compliance_status()
        self.assertEqual(eq.status, Equipment.Status.AVAILABLE)

    def test_renewal_restores_allocated_not_available_when_still_on_loan(self):
        eq = self.make()
        mv = Movement.objects.create(
            equipment=eq,
            movement_type=Movement.Type.ALLOCATE,
            from_location=self.apron,
            to_location=self.workshop,
            allocated_by=self.user,
        )
        mv.approve(self.user)
        eq.refresh_from_db()
        self.assertEqual(eq.status, Equipment.Status.ALLOCATED)

        eq.tuv_expiry_date = self.today - timedelta(days=1)
        eq.apply_compliance_status()
        self.assertEqual(eq.status, Equipment.Status.TUV_EXPIRED)

        eq.tuv_expiry_date = self.today + timedelta(days=100)
        eq.apply_compliance_status()
        self.assertEqual(eq.status, Equipment.Status.ALLOCATED)

    def test_compliance_never_overrides_a_supervisors_manual_status(self):
        """A lapsed TUV must not silently pull a unit out of the workshop or un-scrap it."""
        for manual in (
            Equipment.Status.MAINTENANCE,
            Equipment.Status.OOS,
            Equipment.Status.SCRAP,
        ):
            eq = self.make(
                number=f"BLT-{manual}",
                status=manual,
                tuv_expiry_date=self.today - timedelta(days=5),
            )
            eq.apply_compliance_status()
            self.assertEqual(eq.status, manual)


class AllocationRuleTests(Base):
    def test_cannot_allocate_a_unit_with_an_expired_certificate(self):
        eq = self.make(ramp_pass_expiry_date=self.today - timedelta(days=2))
        eq.apply_compliance_status()
        form = MovementForm(
            data={
                "movement_type": Movement.Type.ALLOCATE,
                "to_location": self.workshop.pk,
                "allocation_date": self.today,
            },
            equipment=eq,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Ramp Pass", str(form.errors))

    def test_cannot_allocate_a_unit_that_is_in_maintenance(self):
        eq = self.make(status=Equipment.Status.MAINTENANCE)
        form = MovementForm(
            data={
                "movement_type": Movement.Type.ALLOCATE,
                "to_location": self.workshop.pk,
                "allocation_date": self.today,
            },
            equipment=eq,
        )
        self.assertFalse(form.is_valid())

    def test_cannot_move_a_unit_to_where_it_already_is(self):
        eq = self.make()
        form = MovementForm(
            data={
                "movement_type": Movement.Type.TRANSFER,
                "to_location": self.apron.pk,
                "allocation_date": self.today,
            },
            equipment=eq,
        )
        self.assertFalse(form.is_valid())

    def test_approval_moves_the_equipment_and_a_return_closes_the_allocation(self):
        eq = self.make()
        out = Movement.objects.create(
            equipment=eq,
            movement_type=Movement.Type.ALLOCATE,
            from_location=self.apron,
            to_location=self.workshop,
            allocated_by=self.user,
        )
        out.approve(self.user)
        eq.refresh_from_db()
        self.assertEqual(eq.location, self.workshop)
        self.assertEqual(eq.status, Equipment.Status.ALLOCATED)

        back = Movement.objects.create(
            equipment=eq,
            movement_type=Movement.Type.RETURN,
            from_location=self.workshop,
            to_location=self.apron,
            allocated_by=self.user,
        )
        back.approve(self.user)
        eq.refresh_from_db()
        out.refresh_from_db()
        self.assertEqual(eq.location, self.apron)
        self.assertEqual(eq.status, Equipment.Status.AVAILABLE)
        self.assertIsNotNone(out.returned_at)

    def test_a_pending_request_does_not_move_anything(self):
        eq = self.make()
        Movement.objects.create(
            equipment=eq,
            movement_type=Movement.Type.ALLOCATE,
            from_location=self.apron,
            to_location=self.workshop,
            allocated_by=self.user,
        )
        eq.refresh_from_db()
        self.assertEqual(eq.location, self.apron)
        self.assertEqual(eq.status, Equipment.Status.AVAILABLE)


class DashboardTests(Base):
    def test_status_counts_are_not_collapsed_by_the_default_ordering(self):
        for i in range(5):
            self.make(number=f"A-{i}", status=Equipment.Status.AVAILABLE)
        for i in range(3):
            self.make(number=f"B-{i}", status=Equipment.Status.OOS)
        stats = services.dashboard_stats()
        self.assertEqual(stats["available"], 5)
        self.assertEqual(stats["oos"], 3)
        self.assertEqual(stats["total"], 8)

    def test_readiness_excludes_scrapped_units(self):
        self.make(number="A-1", status=Equipment.Status.AVAILABLE)
        self.make(number="A-2", status=Equipment.Status.OOS)
        self.make(number="A-3", status=Equipment.Status.SCRAP)
        stats = services.dashboard_stats()
        # 1 serviceable of 2 active units -- the scrapped one is off the books.
        self.assertEqual(stats["fleet"], 2)
        self.assertEqual(stats["readiness"], 50.0)

    def test_expiry_buckets_do_not_double_count(self):
        self.make(number="E-1", tuv_expiry_date=self.today - timedelta(days=1))
        self.make(number="E-2", tuv_expiry_date=self.today + timedelta(days=10))
        self.make(number="E-3", tuv_expiry_date=self.today + timedelta(days=200))
        stats = services.dashboard_stats()
        self.assertEqual(stats["expired"], 1)
        self.assertEqual(stats["upcoming"], 1)

    def test_a_unit_expiring_on_two_certificates_is_counted_once(self):
        self.make(
            number="E-9",
            tuv_expiry_date=self.today - timedelta(days=1),
            pm_due_date=self.today - timedelta(days=3),
        )
        self.assertEqual(services.dashboard_stats()["expired"], 1)


class OutOfOperationTests(Base):
    """The ageing column the workbook renders as #NAME?, and the guarantee that
    the daily report can never disagree with the OOS list again."""

    def test_days_out_counts_from_the_start_date(self):
        eq = self.make()
        ev = ServiceEvent.objects.create(
            equipment=eq,
            reason=ServiceEvent.Reason.REPAIR,
            start_date=self.today - timedelta(days=120),
        )
        self.assertEqual(ev.days_out(self.today), 120)
        self.assertEqual(ev.age_bucket, "90+")

    def test_a_closed_event_stops_ageing_on_the_day_it_closed(self):
        eq = self.make()
        ev = ServiceEvent.objects.create(
            equipment=eq,
            reason=ServiceEvent.Reason.REPAIR,
            start_date=self.today - timedelta(days=30),
            closed_on=self.today - timedelta(days=10),
        )
        self.assertEqual(ev.days_out(self.today), 20)

    def test_closing_the_last_event_puts_the_unit_back_in_service(self):
        eq = self.make(status=Equipment.Status.MAINTENANCE)
        ev = ServiceEvent.objects.create(
            equipment=eq,
            reason=ServiceEvent.Reason.REPAIR,
            start_date=self.today - timedelta(days=5),
        )
        ev.close(on=self.today)
        eq.refresh_from_db()
        self.assertEqual(eq.status, Equipment.Status.AVAILABLE)

    def test_a_unit_out_twice_stays_out_until_the_last_event_closes(self):
        eq = self.make(status=Equipment.Status.MAINTENANCE)
        first = ServiceEvent.objects.create(
            equipment=eq,
            reason=ServiceEvent.Reason.REPAIR,
            start_date=self.today - timedelta(days=5),
        )
        ServiceEvent.objects.create(
            equipment=eq,
            reason=ServiceEvent.Reason.TUV,
            start_date=self.today - timedelta(days=2),
        )
        first.close(on=self.today)
        eq.refresh_from_db()
        self.assertEqual(eq.status, Equipment.Status.MAINTENANCE)

    def test_daily_report_oos_always_equals_the_open_oos_list(self):
        """The whole point of the rebuild. In the workbook these two numbers are
        typed separately and today they disagree (the pivot says 117, the list
        says 113). Here they are the same query, so they cannot drift."""
        for i in range(7):
            eq = self.make(number=f"BLT-1{i:02}")
            ServiceEvent.objects.create(
                equipment=eq,
                reason=ServiceEvent.Reason.REPAIR,
                start_date=self.today - timedelta(days=i + 1),
            )
        # one that came back -- must not be counted
        eq = self.make(number="BLT-200")
        ServiceEvent.objects.create(
            equipment=eq,
            reason=ServiceEvent.Reason.PM,
            start_date=self.today - timedelta(days=9),
            closed_on=self.today,
        )
        report = services.daily_report(as_of=self.today)
        register = services.oos_register(as_of=self.today)
        self.assertEqual(report["totals"]["oos"], 7)
        self.assertEqual(len(register), 7)
        self.assertEqual(report["totals"]["oos"], len(register))

    def test_in_service_is_actual_count_minus_units_out(self):
        for i in range(4):
            self.make(number=f"BLT-3{i:02}")
        eq = self.make(number="BLT-400")
        ServiceEvent.objects.create(
            equipment=eq,
            reason=ServiceEvent.Reason.TUV,
            start_date=self.today,
        )
        t = services.daily_report(as_of=self.today)["totals"]
        self.assertEqual(t["actual"], 5)
        self.assertEqual(t["oos"], 1)
        self.assertEqual(t["in_service"], 4)
        self.assertEqual(t["in_service_pct"], 80.0)

    def test_grand_total_follows_the_stations_own_arithmetic(self):
        """Their sheet computes G = C - D + E + F. Overage and borrowed units are
        added to the fleet, units lent to another station are taken off it."""
        self.make(number="BLT-501")
        self.make(number="BLT-502", is_overage=True)
        self.make(number="BLT-503", ownership=Equipment.Ownership.LOCAL_SUPPORT)
        self.make(number="BLT-504", ownership=Equipment.Ownership.OUT_STATION)
        t = services.daily_report(as_of=self.today)["totals"]
        self.assertEqual(t["actual"], 4)
        self.assertEqual(t["overage"], 1)
        self.assertEqual(t["from_local"], 1)
        self.assertEqual(t["to_out_station"], 1)
        # C = actual - from_local + to_out_station - overage
        self.assertEqual(t["grand_total"], 4 - 1 + 1 - 1)

    def test_ageing_buckets_do_not_double_count(self):
        spans = [1, 5, 20, 45, 75, 200, 400]
        for i, d in enumerate(spans):
            eq = self.make(number=f"BLT-6{i:02}")
            ServiceEvent.objects.create(
                equipment=eq,
                reason=ServiceEvent.Reason.REPAIR,
                start_date=self.today - timedelta(days=d),
            )
        a = services.oos_aging(as_of=self.today)
        self.assertEqual(sum(b["n"] for b in a["buckets"]), len(spans))
        self.assertEqual(a["stale_count"], 2)  # 200 and 400 days
        self.assertEqual(a["max_days"], 400)
