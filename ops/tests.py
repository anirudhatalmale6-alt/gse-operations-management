from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from ops import services
from ops.forms import MovementForm
from ops.models import Department, Equipment, EquipmentType, Location, Movement


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
