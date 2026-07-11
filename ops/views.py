import csv
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import services
from .forms import (
    DepartmentForm,
    EmployeeForm,
    EquipmentDocumentForm,
    EquipmentForm,
    LocationForm,
    MovementForm,
)
from .models import (
    Department,
    Employee,
    Equipment,
    EquipmentType,
    Location,
    Movement,
    TrainingRecord,
)


def _filtered_equipment(request):
    """Apply the shared Date / Location / Department / Type / Status filter bar."""
    qs = Equipment.objects.select_related(
        "equipment_type", "location", "department"
    )
    f = {
        "location": request.GET.get("location", ""),
        "department": request.GET.get("department", ""),
        "equipment_type": request.GET.get("equipment_type", ""),
        "status": request.GET.get("status", ""),
        "q": request.GET.get("q", "").strip(),
        "expiry": request.GET.get("expiry", ""),
    }
    if f["location"]:
        qs = qs.filter(location_id=f["location"])
    if f["department"]:
        qs = qs.filter(department_id=f["department"])
    if f["equipment_type"]:
        qs = qs.filter(equipment_type_id=f["equipment_type"])
    if f["status"]:
        qs = qs.filter(status=f["status"])
    if f["q"]:
        qs = qs.filter(
            Q(equipment_number__icontains=f["q"])
            | Q(serial_number__icontains=f["q"])
            | Q(registration_number__icontains=f["q"])
            | Q(model__icontains=f["q"])
            | Q(manufacturer__icontains=f["q"])
        )
    today = timezone.localdate()
    if f["expiry"] == "expired":
        qs = qs.filter(services._expiry_q(today)).exclude(status=Equipment.Status.SCRAP)
    elif f["expiry"] == "upcoming":
        horizon = today + timedelta(days=settings.EXPIRY_WARNING_DAYS)
        qs = (
            qs.filter(services._expiry_q(today, horizon))
            .exclude(services._expiry_q(today))
            .exclude(status=Equipment.Status.SCRAP)
        )
    return qs.distinct(), f


def _filter_options():
    return {
        "locations": Location.objects.filter(active=True),
        "departments": Department.objects.filter(active=True),
        "equipment_types": EquipmentType.objects.filter(active=True),
        "statuses": Equipment.Status.choices,
    }


# ------------------------------------------------------------------------ dashboard


@login_required
def dashboard(request):
    qs, f = _filtered_equipment(request)
    labels, values = services.readiness_trend()
    ctx = {
        "stats": services.dashboard_stats(qs),
        "locations_table": services.location_breakdown(qs),
        "pending": services.pending_approvals()[:8],
        "expiring": (
            qs.filter(
                services._expiry_q(
                    timezone.localdate(),
                    timezone.localdate() + timedelta(days=settings.EXPIRY_WARNING_DAYS),
                )
                | services._expiry_q(timezone.localdate())
            )
            .exclude(status=Equipment.Status.SCRAP)
            .distinct()[:10]
        ),
        "recent_movements": Movement.objects.select_related(
            "equipment", "from_location", "to_location"
        )[:8],
        "trend_labels": labels,
        "trend_values": values,
        "f": f,
        **_filter_options(),
    }
    return render(request, "ops/dashboard.html", ctx)


# ------------------------------------------------------------------------ equipment


@login_required
def equipment_list(request):
    qs, f = _filtered_equipment(request)
    ctx = {"equipment": qs, "f": f, "total": qs.count(), **_filter_options()}
    return render(request, "ops/equipment_list.html", ctx)


@login_required
def equipment_detail(request, pk):
    eq = get_object_or_404(
        Equipment.objects.select_related("equipment_type", "location", "department"),
        pk=pk,
    )
    doc_form = EquipmentDocumentForm()
    move_form = MovementForm(equipment=eq)
    if request.method == "POST" and "upload_document" in request.POST:
        doc_form = EquipmentDocumentForm(request.POST, request.FILES)
        if doc_form.is_valid():
            doc = doc_form.save(commit=False)
            doc.equipment = eq
            doc.uploaded_by = request.user
            doc.save()
            messages.success(request, f"Document '{doc.title}' uploaded.")
            return redirect("equipment_detail", pk=eq.pk)
    ctx = {
        "eq": eq,
        "compliance": eq.compliance_items(),
        "movements": eq.movements.select_related(
            "from_location", "to_location", "allocated_by", "approved_by"
        ),
        "doc_form": doc_form,
        "move_form": move_form,
    }
    return render(request, "ops/equipment_detail.html", ctx)


@login_required
def equipment_create(request):
    form = EquipmentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        eq = form.save()
        eq.apply_compliance_status()
        messages.success(request, f"Equipment {eq.equipment_number} created.")
        return redirect("equipment_detail", pk=eq.pk)
    return render(
        request, "ops/equipment_form.html", {"form": form, "title": "Add Equipment"}
    )


@login_required
def equipment_edit(request, pk):
    eq = get_object_or_404(Equipment, pk=pk)
    form = EquipmentForm(request.POST or None, request.FILES or None, instance=eq)
    if request.method == "POST" and form.is_valid():
        eq = form.save()
        eq.apply_compliance_status()
        messages.success(request, f"Equipment {eq.equipment_number} updated.")
        return redirect("equipment_detail", pk=eq.pk)
    return render(
        request,
        "ops/equipment_form.html",
        {"form": form, "title": f"Edit {eq.equipment_number}", "eq": eq},
    )


@login_required
def equipment_export(request):
    qs, _ = _filtered_equipment(request)
    response = HttpResponse(content_type="text/csv")
    stamp = timezone.localdate().isoformat()
    response["Content-Disposition"] = f'attachment; filename="gse_equipment_{stamp}.csv"'
    w = csv.writer(response)
    w.writerow([
        "Equipment Number", "Type", "Model", "Manufacturer", "Serial Number",
        "Registration Number", "Purchase Date", "Location", "Station", "Department",
        "Status", "PM Due", "TUV Expiry", "Ramp Pass Expiry", "Insurance Expiry",
    ])
    for e in qs:
        w.writerow([
            e.equipment_number, e.equipment_type.name, e.model, e.manufacturer,
            e.serial_number, e.registration_number, e.purchase_date or "",
            e.location.name, e.location.station, e.department.name,
            e.get_status_display(), e.pm_due_date or "", e.tuv_expiry_date or "",
            e.ramp_pass_expiry_date or "", e.insurance_expiry_date or "",
        ])
    return response


# ----------------------------------------------------------------------- allocation


@login_required
def movement_create(request, pk):
    eq = get_object_or_404(Equipment, pk=pk)
    form = MovementForm(request.POST or None, equipment=eq)
    if request.method == "POST" and form.is_valid():
        mv = form.save(commit=False)
        mv.equipment = eq
        mv.from_location = eq.location
        mv.allocated_by = request.user
        if not mv.allocation_time:
            mv.allocation_time = timezone.localtime().time()
        mv.save()
        messages.success(
            request,
            f"{mv.get_movement_type_display()} request raised for "
            f"{eq.equipment_number}. Awaiting supervisor approval.",
        )
        return redirect("equipment_detail", pk=eq.pk)
    if request.method == "POST":
        for err in form.non_field_errors():
            messages.error(request, err)
    return redirect("equipment_detail", pk=eq.pk)


@login_required
def movement_list(request):
    qs = Movement.objects.select_related(
        "equipment", "from_location", "to_location", "allocated_by", "approved_by"
    )
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)
    return render(
        request,
        "ops/movement_list.html",
        {"movements": qs, "status": status, "statuses": Movement.Status.choices},
    )


@login_required
def movement_action(request, pk, action):
    mv = get_object_or_404(Movement, pk=pk)
    if mv.status != Movement.Status.PENDING:
        messages.warning(request, "That request has already been actioned.")
    elif action == "approve":
        mv.approve(request.user)
        messages.success(
            request,
            f"Approved. {mv.equipment.equipment_number} is now at {mv.to_location} "
            f"({mv.equipment.get_status_display()}).",
        )
    elif action == "reject":
        mv.reject(request.user)
        messages.info(request, f"Rejected the request for {mv.equipment.equipment_number}.")
    return redirect(request.META.get("HTTP_REFERER", "movement_list"))


# -------------------------------------------------------------------------- masters


@login_required
def location_list(request):
    locations = services.location_breakdown()
    return render(request, "ops/location_list.html", {"locations": locations})


@login_required
def location_detail(request, pk):
    loc = get_object_or_404(Location, pk=pk)
    equipment = loc.equipment_here.select_related("equipment_type", "department")
    return render(
        request,
        "ops/location_detail.html",
        {
            "loc": loc,
            "equipment": equipment,
            "stats": services.dashboard_stats(equipment),
            "movements": Movement.objects.filter(
                Q(from_location=loc) | Q(to_location=loc)
            ).select_related("equipment", "from_location", "to_location")[:20],
        },
    )


@login_required
def location_create(request):
    form = LocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        loc = form.save()
        messages.success(request, f"Location {loc} created.")
        return redirect("location_list")
    return render(
        request, "ops/simple_form.html", {"form": form, "title": "Add Location"}
    )


@login_required
def location_edit(request, pk):
    loc = get_object_or_404(Location, pk=pk)
    form = LocationForm(request.POST or None, instance=loc)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Location {loc} updated.")
        return redirect("location_list")
    return render(
        request, "ops/simple_form.html", {"form": form, "title": f"Edit {loc}"}
    )


@login_required
def employee_list(request):
    employees = Employee.objects.select_related("department", "base_location")
    q = request.GET.get("q", "").strip()
    if q:
        employees = employees.filter(
            Q(full_name__icontains=q) | Q(employee_number__icontains=q)
        )
    return render(
        request, "ops/employee_list.html", {"employees": employees, "q": q}
    )


@login_required
def employee_detail(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    return render(
        request,
        "ops/employee_detail.html",
        {
            "emp": emp,
            "training": emp.training_records.select_related("course"),
            "supervised": emp.supervised_locations.all(),
        },
    )


@login_required
def employee_create(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        emp = form.save()
        messages.success(request, f"Employee {emp.full_name} created.")
        return redirect("employee_detail", pk=emp.pk)
    return render(
        request, "ops/simple_form.html", {"form": form, "title": "Add Employee"}
    )


@login_required
def department_list(request):
    form = DepartmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Department saved.")
        return redirect("department_list")
    return render(
        request,
        "ops/department_list.html",
        {"departments": Department.objects.all(), "form": form},
    )


# ------------------------------------------------------------------------ compliance


@login_required
def compliance(request):
    today = timezone.localdate()
    horizon = today + timedelta(days=settings.EXPIRY_WARNING_DAYS)
    base = Equipment.objects.select_related("equipment_type", "location").exclude(
        status=Equipment.Status.SCRAP
    )
    expired = base.filter(services._expiry_q(today)).distinct()
    upcoming = (
        base.filter(services._expiry_q(today, horizon))
        .exclude(services._expiry_q(today))
        .distinct()
    )
    training = TrainingRecord.objects.select_related("employee", "course").filter(
        expires_on__lte=horizon
    )
    return render(
        request,
        "ops/compliance.html",
        {
            "expired": expired,
            "upcoming": upcoming,
            "training": training,
            "warning_days": settings.EXPIRY_WARNING_DAYS,
        },
    )
