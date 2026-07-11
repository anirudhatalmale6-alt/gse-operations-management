from django.contrib import admin

from .models import (
    Department,
    Employee,
    Equipment,
    EquipmentDocument,
    EquipmentType,
    Location,
    Movement,
    ReadinessSnapshot,
    TrainingCourse,
    TrainingRecord,
)


class EquipmentDocumentInline(admin.TabularInline):
    model = EquipmentDocument
    extra = 0


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = (
        "equipment_number", "equipment_type", "location", "department", "status",
        "pm_due_date", "tuv_expiry_date", "ramp_pass_expiry_date",
    )
    list_filter = ("status", "equipment_type", "location", "department")
    search_fields = ("equipment_number", "serial_number", "registration_number", "model")
    inlines = [EquipmentDocumentInline]


@admin.register(Movement)
class MovementAdmin(admin.ModelAdmin):
    list_display = (
        "equipment", "movement_type", "from_location", "to_location",
        "allocation_date", "status", "allocated_by", "approved_by",
    )
    list_filter = ("status", "movement_type", "to_location")


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "station", "area", "zone", "department", "supervisor", "active")
    list_filter = ("station", "department", "active")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_number", "full_name", "designation", "department", "status")
    list_filter = ("department", "status")
    search_fields = ("employee_number", "full_name")


@admin.register(TrainingRecord)
class TrainingRecordAdmin(admin.ModelAdmin):
    list_display = ("employee", "course", "completed_on", "expires_on")
    list_filter = ("course",)


admin.site.register(Department)
admin.site.register(EquipmentType)
admin.site.register(TrainingCourse)
admin.site.register(ReadinessSnapshot)

admin.site.site_header = "GSE Operations Management"
admin.site.site_title = "GSE OMS"
