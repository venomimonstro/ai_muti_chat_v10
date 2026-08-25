from django.core.management.base import BaseCommand, CommandError

from apps.billing.reconciliation import reconcile_billing


class Command(BaseCommand):
    help = "Reconcile wallet ledger and completed AI request costs"

    def handle(self, *args, **options):
        try:
            run = reconcile_billing()
        except Exception as exc:
            raise CommandError(f"Billing reconciliation failed: {type(exc).__name__}") from exc
        self.stdout.write(
            f"run={run.id} status={run.status} wallets={run.checked_wallets} "
            f"requests={run.checked_requests} discrepancies={run.discrepancy_count}"
        )
