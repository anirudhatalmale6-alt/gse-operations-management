from django.utils import timezone

from .models import Movement


def alerts(request):
    """Badge counts shown in the sidebar on every page."""
    if not request.user.is_authenticated:
        return {}
    from . import services

    today = timezone.localdate()
    stats = services.dashboard_stats(today=today)
    return {
        "nav_pending_count": Movement.objects.filter(
            status=Movement.Status.PENDING
        ).count(),
        "nav_compliance_count": stats["expired"] + stats["upcoming"],
    }
