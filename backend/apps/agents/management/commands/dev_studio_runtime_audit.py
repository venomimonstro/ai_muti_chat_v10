from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.billing.models import BalanceReservation
from apps.procurement.models import ProviderSpendReservation

from apps.agents.models import AgentApproval, AgentRun, AgentStepRun, AgentTeam
from apps.agents.sandbox_client import sandbox_health


ACTIVE_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.REVIEWING,
}


class Command(BaseCommand):
    help = "Audit Dev Studio execution plane, stale work and accounting reservations"

    def handle(self, *args, **options):
        failures = []
        warnings = []
        now = timezone.now()
        since = now - timedelta(hours=24)

        self.stdout.write("=== DEV STUDIO RUNTIME AUDIT ===")

        try:
            health = sandbox_health()
        except Exception as exc:
            health = {"ok": False, "error": str(exc)[:500]}
        self.stdout.write(f"sandbox_ok={bool(health.get('ok'))}")
        if not health.get("ok"):
            failures.append(f"sandbox_unavailable: {health.get('error') or 'health probe failed'}")

        dev_teams = AgentTeam.objects.filter(kind=AgentTeam.Kind.DEVELOPMENT, active=True)
        active_runs = AgentRun.objects.filter(team__kind=AgentTeam.Kind.DEVELOPMENT, state__in=ACTIVE_STATES)
        recent_runs = AgentRun.objects.filter(team__kind=AgentTeam.Kind.DEVELOPMENT, created_at__gte=since)
        failed_runs = recent_runs.filter(state=AgentRun.State.FAILED)
        waiting_approvals = AgentApproval.objects.filter(
            run__team__kind=AgentTeam.Kind.DEVELOPMENT,
            status=AgentApproval.Status.PENDING,
        )

        self.stdout.write(f"active_dev_teams={dev_teams.count()}")
        self.stdout.write(f"active_dev_runs={active_runs.count()}")
        self.stdout.write(f"recent_24h_runs={recent_runs.count()}")
        self.stdout.write(f"recent_24h_failed={failed_runs.count()}")
        self.stdout.write(f"pending_approvals={waiting_approvals.count()}")

        for run in active_runs.select_related("team").iterator(chunk_size=200):
            reference = run.updated_at or run.started_at or run.created_at
            if reference < now - timedelta(hours=4):
                failures.append(f"stale_run={run.id} state={run.state} updated_at={reference.isoformat()}")

        failed_steps = AgentStepRun.objects.filter(
            run__team__kind=AgentTeam.Kind.DEVELOPMENT,
            state=AgentStepRun.State.FAILED,
            created_at__gte=since,
        ).select_related("run")
        self.stdout.write(f"recent_24h_failed_steps={failed_steps.count()}")

        provider_attempts = 0
        fallback_steps = 0
        for step in AgentStepRun.objects.filter(
            run__team__kind=AgentTeam.Kind.DEVELOPMENT,
            created_at__gte=since,
        ).only("output_payload").iterator(chunk_size=500):
            payload = step.output_payload or {}
            attempts = payload.get("model_attempts") or []
            if isinstance(attempts, list):
                provider_attempts += len(attempts)
                if len(attempts) > 1:
                    fallback_steps += 1
            elif payload.get("provider_attempts"):
                try:
                    provider_attempts += int(payload.get("provider_attempts") or 0)
                except (TypeError, ValueError):
                    pass
        self.stdout.write(f"provider_model_attempts_24h={provider_attempts}")
        self.stdout.write(f"fallback_steps_24h={fallback_steps}")

        active_customer_reservations = BalanceReservation.objects.filter(
            state=BalanceReservation.State.ACTIVE,
            idempotency_key__startswith="agent-run:",
        )
        active_provider_reservations = ProviderSpendReservation.objects.filter(
            state=ProviderSpendReservation.State.ACTIVE,
            source_key__startswith="agent:",
        )
        self.stdout.write(f"active_customer_reservations={active_customer_reservations.count()}")
        self.stdout.write(f"active_provider_reservations={active_provider_reservations.count()}")

        orphan_cutoff = now - timedelta(minutes=30)
        for reservation in active_customer_reservations.filter(created_at__lt=orphan_cutoff).iterator(chunk_size=200):
            warnings.append(f"old_customer_reservation={reservation.id} created_at={reservation.created_at.isoformat()}")
        for reservation in active_provider_reservations.filter(created_at__lt=orphan_cutoff).iterator(chunk_size=200):
            warnings.append(f"old_provider_reservation={reservation.id} created_at={reservation.created_at.isoformat()}")

        for warning in warnings[:100]:
            self.stdout.write(self.style.WARNING(f"[WARN] {warning}"))
        for failure in failures[:100]:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))

        if failures:
            raise CommandError(f"Dev Studio runtime audit failed: {len(failures)} critical issue(s)")
        self.stdout.write(self.style.SUCCESS("DEV_STUDIO_RUNTIME_AUDIT_OK"))
