"""Core domain models for the GSE Operations Management System."""

from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django.utils import timezone


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# --------------------------------------------------------------------------- masters


class Department(TimeStamped):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class EquipmentType(TimeStamped):
    """e.g. Belt Loader, Pushback Tug, GPU, ULD Loader, Water Truck."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    powered = models.BooleanField(
        default=True, help_text="Motorised equipment needs a ramp pass and TUV."
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Location(TimeStamped):
    """Location master: Location / Station / Area / Zone / Department / Supervisor."""

    name = models.CharField(max_length=100)
    station = models.CharField(max_length=10, help_text="IATA station code, e.g. DXB")
    area = models.CharField(max_length=60, blank=True)
    zone = models.CharField(max_length=60, blank=True)
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="locations"
    )
    supervisor = models.ForeignKey(
        "Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supervised_locations",
        verbose_name="Responsible supervisor",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["station", "name"]
        unique_together = [("station", "name")]

    def __str__(self):
        return f"{self.station} - {self.name}"

    def get_absolute_url(self):
        return reverse("location_detail", args=[self.pk])


class Employee(TimeStamped):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ON_LEAVE = "ON_LEAVE", "On Leave"
        INACTIVE = "INACTIVE", "Inactive"

    employee_number = models.CharField(max_length=30, unique=True)
    full_name = models.CharField(max_length=120)
    designation = models.CharField(max_length=80, blank=True)
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="employees"
    )
    base_location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="based_employees",
    )
    phone = models.CharField(max_length=30, blank=True)
    joined_on = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee",
        help_text="Optional login account for this employee.",
    )

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.employee_number} - {self.full_name}"

    def get_absolute_url(self):
        return reverse("employee_detail", args=[self.pk])


# ------------------------------------------------------------------------- equipment


class Equipment(TimeStamped):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        ALLOCATED = "ALLOCATED", "Allocated"
        MAINTENANCE = "MAINTENANCE", "Maintenance"
        PM_DUE = "PM_DUE", "PM Due"
        TUV_EXPIRED = "TUV_EXPIRED", "TUV Expired"
        RAMP_PASS_EXPIRED = "RAMP_PASS_EXPIRED", "Ramp Pass Expired"
        OOS = "OOS", "OOS"
        SCRAP = "SCRAP", "Scrap"

    # Statuses that mean the unit can actually be used on the ramp today.
    SERVICEABLE = {Status.AVAILABLE, Status.ALLOCATED}
    # Statuses the compliance engine owns and may recalculate.
    COMPLIANCE_DRIVEN = {Status.PM_DUE, Status.TUV_EXPIRED, Status.RAMP_PASS_EXPIRED}
    # Statuses a supervisor owns -- the compliance engine must never overwrite these.
    MANUAL_ONLY = {Status.MAINTENANCE, Status.OOS, Status.SCRAP}

    equipment_number = models.CharField(max_length=40, unique=True, db_index=True)
    equipment_type = models.ForeignKey(
        EquipmentType, on_delete=models.PROTECT, related_name="equipment"
    )
    model = models.CharField(max_length=80, blank=True)
    manufacturer = models.CharField(max_length=80, blank=True)
    serial_number = models.CharField(max_length=80, blank=True)
    registration_number = models.CharField(max_length=40, blank=True)
    purchase_date = models.DateField(null=True, blank=True)

    location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="equipment_here",
        verbose_name="Current location",
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="equipment"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.AVAILABLE, db_index=True
    )

    pm_due_date = models.DateField(null=True, blank=True, verbose_name="PM due date")
    tuv_expiry_date = models.DateField(null=True, blank=True, verbose_name="TUV expiry")
    ramp_pass_expiry_date = models.DateField(
        null=True, blank=True, verbose_name="Ramp pass expiry"
    )
    insurance_expiry_date = models.DateField(
        null=True, blank=True, verbose_name="Insurance expiry"
    )

    photo = models.ImageField(upload_to="equipment/photos/", blank=True, null=True)
    remarks = models.TextField(blank=True)

    COMPLIANCE_FIELDS = (
        ("pm_due_date", "PM", Status.PM_DUE),
        ("tuv_expiry_date", "TUV", Status.TUV_EXPIRED),
        ("ramp_pass_expiry_date", "Ramp Pass", Status.RAMP_PASS_EXPIRED),
        ("insurance_expiry_date", "Insurance", None),
    )

    class Meta:
        ordering = ["equipment_number"]
        verbose_name_plural = "Equipment"

    def __str__(self):
        return self.equipment_number

    def get_absolute_url(self):
        return reverse("equipment_detail", args=[self.pk])

    # -- compliance ---------------------------------------------------------

    def compliance_items(self, today=None):
        """One row per compliance date: label, date, days remaining, state."""
        today = today or timezone.localdate()
        warn = settings.EXPIRY_WARNING_DAYS
        items = []
        for field, label, _status in self.COMPLIANCE_FIELDS:
            due = getattr(self, field)
            if due is None:
                items.append({"label": label, "date": None, "days": None, "state": "unset"})
                continue
            days = (due - today).days
            if days < 0:
                state = "expired"
            elif days <= warn:
                state = "due_soon"
            else:
                state = "ok"
            items.append({"label": label, "date": due, "days": days, "state": state})
        return items

    @property
    def expired_items(self):
        return [i for i in self.compliance_items() if i["state"] == "expired"]

    @property
    def due_soon_items(self):
        return [i for i in self.compliance_items() if i["state"] == "due_soon"]

    @property
    def is_serviceable(self):
        return self.status in self.SERVICEABLE

    def derived_status(self, today=None):
        """The status the compliance dates imply, or None if they imply nothing.

        Statuses a supervisor sets by hand (Maintenance, OOS, Scrap) always win: a
        lapsed TUV must never silently pull a unit out of the workshop or un-scrap it.
        """
        if self.status in self.MANUAL_ONLY:
            return None
        today = today or timezone.localdate()
        for field, _label, status in self.COMPLIANCE_FIELDS:
            if status is None:
                continue
            due = getattr(self, field)
            if due and due < today:
                return status
        return None

    def apply_compliance_status(self, today=None, save=True):
        """Sync status against the compliance dates. Returns True if it changed."""
        if self.status in self.MANUAL_ONLY:
            return False
        derived = self.derived_status(today)
        new = derived
        if derived is None and self.status in self.COMPLIANCE_DRIVEN:
            # Dates were renewed: put the unit back into service where it stands.
            has_open_allocation = self.movements.filter(
                movement_type=Movement.Type.ALLOCATE,
                status=Movement.Status.APPROVED,
                returned_at__isnull=True,
            ).exists()
            new = (
                self.Status.ALLOCATED if has_open_allocation else self.Status.AVAILABLE
            )
        if new and new != self.status:
            self.status = new
            if save:
                self.save(update_fields=["status", "updated_at"])
            return True
        return False


class EquipmentDocument(TimeStamped):
    class DocType(models.TextChoices):
        TUV = "TUV", "TUV Certificate"
        RAMP_PASS = "RAMP_PASS", "Ramp Pass"
        INSURANCE = "INSURANCE", "Insurance"
        REGISTRATION = "REGISTRATION", "Registration"
        MANUAL = "MANUAL", "Manual"
        OTHER = "OTHER", "Other"

    equipment = models.ForeignKey(
        Equipment, on_delete=models.CASCADE, related_name="documents"
    )
    doc_type = models.CharField(
        max_length=20, choices=DocType.choices, default=DocType.OTHER
    )
    title = models.CharField(max_length=120)
    file = models.FileField(upload_to="equipment/documents/")
    expiry_date = models.DateField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.equipment.equipment_number} - {self.title}"


# ------------------------------------------------------------------------ allocation


class Movement(TimeStamped):
    """One row per allocate / transfer / return request. This is the movement trail."""

    class Type(models.TextChoices):
        ALLOCATE = "ALLOCATE", "Allocate"
        TRANSFER = "TRANSFER", "Transfer"
        RETURN = "RETURN", "Return"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Approval"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    equipment = models.ForeignKey(
        Equipment, on_delete=models.CASCADE, related_name="movements"
    )
    movement_type = models.CharField(max_length=10, choices=Type.choices)

    from_location = models.ForeignKey(
        Location,
        on_delete=models.PROTECT,
        related_name="movements_out",
        null=True,
        blank=True,
    )
    to_location = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="movements_in"
    )

    allocation_date = models.DateField(default=date.today)
    allocation_time = models.TimeField(null=True, blank=True)

    allocated_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="movements_raised"
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movements_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)

    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )

    class Meta:
        ordering = ["-allocation_date", "-created_at"]

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.equipment} to {self.to_location}"

    @property
    def is_open_allocation(self):
        return (
            self.movement_type == self.Type.ALLOCATE
            and self.status == self.Status.APPROVED
            and self.returned_at is None
        )

    @staticmethod
    def _display_name(user):
        if not user:
            return ""
        return user.get_full_name() or user.username

    @property
    def allocated_by_name(self):
        return self._display_name(self.allocated_by)

    @property
    def approved_by_name(self):
        return self._display_name(self.approved_by)

    def approve(self, user):
        """Approve the request and actually move the equipment."""
        self.status = self.Status.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save()

        eq = self.equipment
        eq.location = self.to_location
        if self.movement_type == self.Type.RETURN:
            eq.status = Equipment.Status.AVAILABLE
            # Close whatever open allocation this return settles.
            eq.movements.filter(
                movement_type=self.Type.ALLOCATE,
                status=self.Status.APPROVED,
                returned_at__isnull=True,
            ).update(returned_at=timezone.now())
        elif self.movement_type == self.Type.ALLOCATE:
            eq.status = Equipment.Status.ALLOCATED
        eq.save(update_fields=["location", "status", "updated_at"])
        # Compliance has the final say over Available/Allocated.
        eq.apply_compliance_status()

    def reject(self, user):
        self.status = self.Status.REJECTED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save()


# -------------------------------------------------------------- training & readiness


class TrainingCourse(TimeStamped):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    validity_months = models.PositiveIntegerField(
        default=24, help_text="How long a pass stays valid."
    )
    equipment_types = models.ManyToManyField(
        EquipmentType,
        blank=True,
        related_name="required_courses",
        help_text="Equipment types this certification licenses an employee to operate.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TrainingRecord(TimeStamped):
    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name="training_records"
    )
    course = models.ForeignKey(
        TrainingCourse, on_delete=models.PROTECT, related_name="records"
    )
    completed_on = models.DateField()
    expires_on = models.DateField(null=True, blank=True)
    certificate = models.FileField(
        upload_to="training/certificates/", blank=True, null=True
    )

    class Meta:
        ordering = ["-completed_on"]

    def save(self, *args, **kwargs):
        if not self.expires_on and self.course_id:
            self.expires_on = self.completed_on + timedelta(
                days=int(self.course.validity_months * 30.44)
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee.full_name} - {self.course.code}"

    @property
    def state(self):
        if not self.expires_on:
            return "ok"
        days = (self.expires_on - timezone.localdate()).days
        if days < 0:
            return "expired"
        if days <= settings.EXPIRY_WARNING_DAYS:
            return "due_soon"
        return "ok"


class ReadinessSnapshot(TimeStamped):
    """Daily readiness, stored so the dashboard can trend it over time."""

    snapshot_date = models.DateField()
    location = models.ForeignKey(
        Location, on_delete=models.CASCADE, null=True, blank=True
    )
    total = models.PositiveIntegerField(default=0)
    serviceable = models.PositiveIntegerField(default=0)
    readiness_pct = models.DecimalField(max_digits=5, decimal_places=1, default=0)

    class Meta:
        ordering = ["-snapshot_date"]
        unique_together = [("snapshot_date", "location")]

    def __str__(self):
        return f"{self.snapshot_date} - {self.readiness_pct}%"
