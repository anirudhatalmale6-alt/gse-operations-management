from django import template

from ops.models import Equipment, Movement

register = template.Library()

STATUS_CLASS = {
    Equipment.Status.AVAILABLE: "bg-success-subtle text-success-emphasis",
    Equipment.Status.ALLOCATED: "bg-primary-subtle text-primary-emphasis",
    Equipment.Status.MAINTENANCE: "bg-warning-subtle text-warning-emphasis",
    Equipment.Status.PM_DUE: "bg-warning-subtle text-warning-emphasis",
    Equipment.Status.TUV_EXPIRED: "bg-danger-subtle text-danger-emphasis",
    Equipment.Status.RAMP_PASS_EXPIRED: "bg-danger-subtle text-danger-emphasis",
    Equipment.Status.OOS: "bg-danger-subtle text-danger-emphasis",
    Equipment.Status.SCRAP: "bg-secondary-subtle text-secondary-emphasis",
}

MOVEMENT_CLASS = {
    Movement.Status.PENDING: "bg-warning-subtle text-warning-emphasis",
    Movement.Status.APPROVED: "bg-success-subtle text-success-emphasis",
    Movement.Status.REJECTED: "bg-secondary-subtle text-secondary-emphasis",
}

COMPLIANCE_CLASS = {
    "expired": "bg-danger-subtle text-danger-emphasis",
    "due_soon": "bg-warning-subtle text-warning-emphasis",
    "ok": "bg-success-subtle text-success-emphasis",
    "unset": "bg-light text-muted",
}


@register.filter
def status_class(value):
    return STATUS_CLASS.get(value, "bg-light text-muted")


@register.filter
def movement_class(value):
    return MOVEMENT_CLASS.get(value, "bg-light text-muted")


@register.filter
def compliance_class(value):
    return COMPLIANCE_CLASS.get(value, "bg-light text-muted")


@register.filter
def readiness_class(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "text-muted"
    if value >= 90:
        return "text-success"
    if value >= 75:
        return "text-warning"
    return "text-danger"


@register.simple_tag(takes_context=True)
def querystring(context, **kwargs):
    """Rebuild the current querystring with some keys replaced -- used by the filter bar."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    return "?" + params.urlencode() if params else ""
