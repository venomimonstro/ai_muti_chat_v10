from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.agents.models import AgentRun


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

        self.stdout.write(f"active_checked={queryset.count()}")
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"[WARN] {warning}"))
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))

        if failures:
            raise CommandError(f"Agent recovery audit failed: {len(failures)} stale run(s)")
        self.stdout.write(self.style.SUCCESS("AGENT_RECOVERY_AUDIT_OK"))
