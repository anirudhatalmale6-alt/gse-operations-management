"""Nightly job: re-evaluate statuses against compliance dates, snapshot readiness.

    0 1 * * *  cd /srv/gse && venv/bin/python manage.py refresh_compliance
"""

from django.core.management.base import BaseCommand

from ops import services


class Command(BaseCommand):
    help = "Recalculate compliance-driven statuses and store today's readiness snapshot."

    def handle(self, *args, **options):
        changed = services.refresh_compliance_statuses()
        services.take_readiness_snapshot()
        self.stdout.write(
            self.style.SUCCESS(
                f"Compliance refreshed: {changed} unit(s) changed status. "
                "Readiness snapshot stored."
            )
        )
