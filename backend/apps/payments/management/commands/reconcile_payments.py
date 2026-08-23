from django.core.management.base import BaseCommand

from apps.payments.services import reconcile_open_payments


class Command(BaseCommand):
    help = "Reconcile non-terminal YooKassa payments with authoritative provider state"

    def handle(self, *args, **options):
        run = reconcile_open_payments()
        self.stdout.write(
            f"run={run.id} checked={run.checked_count} corrected={run.corrected_count} "
            f"errors={run.error_count}"
        )
