from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.agents.models import AgentPlanOperation, AgentRun


CHECKED_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.REVIEWING,
}


class Command(BaseCommand):
    help = "Fail on Agent/Dev runs that exceeded their recoverable runtime window"

    def handle(self, *args, **options):
        failures = []
        warnings = []
        now = timezone.now()

        self.stdout.write("=== AGENT RECOVERY AUDIT ===")

        queryset = AgentRun.objects.filter(state__in=CHECKED_STATES).select_related("agent", "team")
        for run in queryset.iterator(chunk_size=300):
            reference = run.started_at or run.created_at
            if run.agent_id:
                allowed_seconds = max(60, int(run.agent.max_runtime_seconds)) + 900
            else:
                allowed_seconds = 4 * 60 * 60
            deadline = reference + timedelta(seconds=allowed_seconds)
            if now > deadline:
                failures.append(
                    f"run={run.id}: state={run.state} exceeded recovery window by "
                    f"{int((now - deadline).total_seconds())}s"
                )
            elif now > reference + timedelta(seconds=max(60, allowed_seconds // 2)):
                warnings.append(f"run={run.id}: long-running state={run.state}")

        settlement_cutoff = now - timedelta(minutes=15)
        stale_settlement_runs = list(
            AgentRun.objects.filter(
                state=AgentRun.State.REVIEWING,
                error_code="agent_settlement_pending",
                updated_at__lt=settlement_cutoff,
            )
            .order_by("updated_at")
            .values_list("id", flat=True)[:200]
        )
        for run_id in stale_settlement_runs:
            failures.append(
                f"run={run_id}: provider/customer settlement reconciliation exceeded 15m"
            )

        planner_cutoff = now - timedelta(minutes=15)
        stale_planner = list(
            AgentPlanOperation.objects.filter(
                state="reconciling",
                updated_at__lt=planner_cutoff,
            )
            .order_by("updated_at")
            .values_list("id", flat=True)[:200]
        )
        for operation_id in stale_planner:
            failures.append(
                f"planner_operation={operation_id}: settlement reconciliation exceeded 15m"
            )

        self.stdout.write(
            f"active_checked={queryset.count()} "
            f"settlement_reconciling_stale={len(stale_settlement_runs)} "
            f"planner_reconciling_stale={len(stale_planner)}"
        )
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"[WARN] {warning}"))
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))

        if failures:
            raise CommandError(f"Agent recovery audit failed: {len(failures)} stale run(s)")
        self.stdout.write(self.style.SUCCESS("AGENT_RECOVERY_AUDIT_OK"))
