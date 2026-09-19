from django.core.management.base import BaseCommand

from apps.payments.services import reconcile_open_payments, reconcile_open_refunds


class Command(BaseCommand):
    help = "Reconcile non-terminal YooKassa payments and refunds with authoritative provider state"

    def handle(self, *args, **options):
        run = reconcile_open_payments()
        refunds = reconcile_open_refunds()
        self.stdout.write(
            f"payments_run={run.id} payments_checked={run.checked_count} "
            f"payments_corrected={run.corrected_count} payments_errors={run.error_count} "
            f"refunds_checked={refunds['checked']} refunds_corrected={refunds['corrected']} "
            f"refunds_errors={refunds['errors']}"
        )
