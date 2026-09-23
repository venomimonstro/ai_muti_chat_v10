from django.core.management.base import BaseCommand, CommandError

from apps.payments.services import reconcile_open_payments, reconcile_open_refunds


class Command(BaseCommand):
    help = "Reconcile non-terminal YooKassa payments and refunds with authoritative provider state"

    def handle(self, *args, **options):
        run = reconcile_open_payments()
        refunds = reconcile_open_refunds()
        summary = (
            f"payments_run={run.id} payments_checked={run.checked_count} "
            f"payments_corrected={run.corrected_count} payments_errors={run.error_count} "
            f"refunds_checked={refunds['checked']} refunds_corrected={refunds['corrected']} "
            f"refunds_errors={refunds['errors']}"
        )
        self.stdout.write(summary)
        if run.error_count or refunds["errors"]:
            raise CommandError("PAYMENT_RECONCILIATION_FAILED")
        self.stdout.write(self.style.SUCCESS("PAYMENT_RECONCILIATION_OK"))
