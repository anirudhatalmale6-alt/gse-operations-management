from django import forms

from .models import (
    Department,
    Employee,
    Equipment,
    EquipmentDocument,
    Location,
    Movement,
)


class BootstrapMixin:
    """Give every widget the right Bootstrap class without repeating it per field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")
            if isinstance(widget, forms.DateInput):
                widget.input_type = "date"
            if isinstance(widget, forms.TimeInput):
                widget.input_type = "time"


class EquipmentForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Equipment
        fields = [
            "equipment_number",
            "equipment_type",
            "model",
            "manufacturer",
            "serial_number",
            "registration_number",
            "purchase_date",
            "location",
            "department",
            "status",
            "pm_due_date",
            "tuv_expiry_date",
            "ramp_pass_expiry_date",
            "insurance_expiry_date",
            "photo",
            "remarks",
        ]
        widgets = {
            "purchase_date": forms.DateInput(),
            "pm_due_date": forms.DateInput(),
            "tuv_expiry_date": forms.DateInput(),
            "ramp_pass_expiry_date": forms.DateInput(),
            "insurance_expiry_date": forms.DateInput(),
            "remarks": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_equipment_number(self):
        # Equipment numbers are handed out on paper in mixed case; normalise so
        # "BL-014" and "bl-014" can never both exist.
        return self.cleaned_data["equipment_number"].strip().upper()


class EquipmentDocumentForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = EquipmentDocument
        fields = ["doc_type", "title", "file", "expiry_date"]
        widgets = {"expiry_date": forms.DateInput()}


class LocationForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Location
        fields = [
            "name",
            "station",
            "area",
            "zone",
            "department",
            "supervisor",
            "active",
        ]


class DepartmentForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Department
        fields = ["code", "name", "active"]


class EmployeeForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Employee
        fields = [
            "employee_number",
            "full_name",
            "designation",
            "department",
            "base_location",
            "phone",
            "joined_on",
            "status",
        ]
        widgets = {"joined_on": forms.DateInput()}


class MovementForm(BootstrapMixin, forms.ModelForm):
    """Raise an Allocate / Transfer / Return request for one piece of equipment."""

    class Meta:
        model = Movement
        fields = [
            "movement_type",
            "to_location",
            "allocation_date",
            "allocation_time",
            "reason",
        ]
        widgets = {
            "allocation_date": forms.DateInput(),
            "allocation_time": forms.TimeInput(),
        }

    def __init__(self, *args, equipment=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.equipment = equipment
        self.fields["to_location"].queryset = Location.objects.filter(active=True)
        self.fields["reason"].widget.attrs["placeholder"] = (
            "e.g. Turnaround support for EK521, Bay 32"
        )

    def clean(self):
        cleaned = super().clean()
        eq = self.equipment
        to_location = cleaned.get("to_location")
        movement_type = cleaned.get("movement_type")
        if not eq:
            return cleaned

        if eq.status == Equipment.Status.SCRAP:
            raise forms.ValidationError(
                "This unit is scrapped and cannot be moved. Change its status first."
            )
        if eq.status in {Equipment.Status.OOS, Equipment.Status.MAINTENANCE} and (
            movement_type == Movement.Type.ALLOCATE
        ):
            raise forms.ValidationError(
                f"{eq.equipment_number} is {eq.get_status_display()} and cannot be "
                "allocated to operations. Return it to service first, or raise a "
                "Transfer to move it to the workshop."
            )
        if movement_type == Movement.Type.ALLOCATE and eq.expired_items:
            labels = ", ".join(i["label"] for i in eq.expired_items)
            raise forms.ValidationError(
                f"Cannot allocate {eq.equipment_number}: {labels} expired. Renew the "
                "certificate before putting this unit back on the ramp."
            )
        if to_location and to_location == eq.location:
            raise forms.ValidationError(
                f"{eq.equipment_number} is already at {to_location}."
            )
        return cleaned
