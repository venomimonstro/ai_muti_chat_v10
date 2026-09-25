from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q, Sum
from django.utils import timezone

from apps.billing.models import BalanceReservation
from apps.procurement.models import ProviderSpendReservation

from apps.agents.models import AgentRun


ACTIVE_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}
TOLERANCE = Decimal("0.0001")


def _customer_reservations(run_id):
    run_id = str(run_id)
    return BalanceReservation.objects.filter(
        Q(idempotency_key=f"agent-run:{run_id}")
        | Q(idempotency_key__startswith=f"agent-run:{run_id}:step:")
        | Q(idempotency_key__startswith=f"agent-team:{run_id}:step:")
        | Q(idempotency_key__startswith=f"agent-graph:{run_id}:"),
        state=BalanceReservation.State.ACTIVE,
    )


def _provider_reservations(run_id):
    run_id = str(run_id)
    return ProviderSpendReservation.objects.filter(
        Q(source_key=f"agent:{run_id}")
        | Q(source_key__startswith=f"agent:{run_id}:step:")
        | Q(source_key__startswith=f"agent-team:{run_id}:step:")
        | Q(source_key__startswith=f"agent-graph:{run_id}:"),
        state=ProviderSpendReservation.State.ACTIVE,
    )


class Command(BaseCommand):
    help = "Verify AgentRun costs against step costs and detect leaked reservations"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        days = max(1, min(int(options["days"]), 3650))
        cutoff = timezone.now() - timedelta(days=days)
        failures = []
        checked = 0

        terminal_runs = (
            AgentRun.objects.exclude(state__in=ACTIVE_STATES)
            .filter(created_at__gte=cutoff)
            .annotate(step_cost_sum=Sum("steps__cost_rub"))
            .order_by("created_at")
        )

        for run in terminal_runs.iterator(chunk_size=200):
            checked += 1
            run_cost = Decimal(run.cost_actual_rub or 0)
            step_cost = Decimal(run.step_cost_sum or 0)
            if abs(run_cost - step_cost) > TOLERANCE:
                failures.append(
                    f"run={run.id}: run cost {run_cost} != step cost sum {step_cost}"
                )

            customer_active = _customer_reservations(run.id).count()
            provider_active = _provider_reservations(run.id).count()
            if customer_active or provider_active:
                failures.append(
                    f"run={run.id}: terminal state={run.state} still has active reservations "
                    f"user={customer_active} provider={provider_active}"
                )

        self.stdout.write(
            f"AGENT_BILLING_CHECKED={checked} WINDOW_DAYS={days} FAILURES={len(failures)}"
        )
        for failure in failures[:200]:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))
        if len(failures) > 200:
            self.stdout.write(self.style.ERROR(f"[FAIL] ... and {len(failures) - 200} more"))
        if failures:
            raise CommandError(f"Agent billing audit failed: {len(failures)} problem(s)")
        self.stdout.write(self.style.SUCCESS("AGENT_BILLING_AUDIT_OK"))
