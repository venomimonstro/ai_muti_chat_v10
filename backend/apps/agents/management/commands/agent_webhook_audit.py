from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.agents.models import Agent
from apps.agents.webhook_models import AgentWebhookDelivery, AgentWebhookTrigger


class Command(BaseCommand):
    help = "Audit Agent Studio event/webhook trigger invariants and stuck deliveries"

    def handle(self, *args, **options):
        failures = []
        warnings = []
        now = timezone.now()

        self.stdout.write("=== AGENT WEBHOOK AUDIT ===")

        triggers = AgentWebhookTrigger.objects.select_related("owner", "agent", "team")
        for trigger in triggers.iterator(chunk_size=200):
            subject = trigger.agent or trigger.team
            if bool(trigger.agent_id) == bool(trigger.team_id):
                failures.append(f"trigger={trigger.id}: exactly one subject is required")
                continue
            if subject is None:
                failures.append(f"trigger={trigger.id}: subject is missing")
                continue
            if subject.owner_id != trigger.owner_id:
                failures.append(f"trigger={trigger.id}: owner mismatch")
            if not str(trigger.secret_hash or "").strip():
                failures.append(f"trigger={trigger.id}: webhook secret hash is empty")
            if trigger.enabled and trigger.agent_id and trigger.agent.status != Agent.Status.ACTIVE:
                failures.append(f"trigger={trigger.id}: enabled webhook points to inactive agent")
            if trigger.enabled and trigger.team_id and not trigger.team.active:
                failures.append(f"trigger={trigger.id}: enabled webhook points to paused team")
            if trigger.enabled and not str(trigger.objective or getattr(subject, "objective", "") or "").strip():
                warnings.append(f"trigger={trigger.id}: no explicit or inherited objective")

        stale_pending_cutoff = now - timedelta(minutes=15)
        old_failed_cutoff = now - timedelta(days=7)
        deliveries = AgentWebhookDelivery.objects.select_related("trigger", "run")
        for delivery in deliveries.iterator(chunk_size=500):
            if delivery.state == AgentWebhookDelivery.State.PENDING:
                if delivery.run_id:
                    failures.append(f"delivery={delivery.id}: pending delivery already has run={delivery.run_id}")
                if delivery.updated_at < stale_pending_cutoff:
                    failures.append(
                        f"delivery={delivery.id}: pending for more than 15 minutes; worker/retry flow may be stuck"
                    )
            elif delivery.state == AgentWebhookDelivery.State.QUEUED:
                if not delivery.run_id:
                    failures.append(f"delivery={delivery.id}: queued delivery has no run")
            elif delivery.state == AgentWebhookDelivery.State.FAILED:
                if not str(delivery.error_message or "").strip():
                    failures.append(f"delivery={delivery.id}: failed delivery has no error message")
                if delivery.updated_at < old_failed_cutoff:
                    warnings.append(f"delivery={delivery.id}: failed delivery is older than 7 days")

            if delivery.run_id:
                if delivery.run.owner_id != delivery.trigger.owner_id:
                    failures.append(f"delivery={delivery.id}: run owner differs from trigger owner")
                payload = delivery.run.input_payload if isinstance(delivery.run.input_payload, dict) else {}
                trigger_id = str(payload.get("webhook_trigger_id") or "")
                delivery_id = str(payload.get("webhook_delivery_id") or "")
                event_id = str(payload.get("event_id") or "")
                if trigger_id and trigger_id != str(delivery.trigger_id):
                    failures.append(f"delivery={delivery.id}: run references another webhook trigger")
                if delivery_id and delivery_id != str(delivery.id):
                    failures.append(f"delivery={delivery.id}: run references another webhook delivery")
                if event_id and event_id != delivery.event_id:
                    failures.append(f"delivery={delivery.id}: run event_id does not match delivery")

        self.stdout.write(
            f"triggers={AgentWebhookTrigger.objects.count()} deliveries={AgentWebhookDelivery.objects.count()}"
        )
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"[WARN] {warning}"))
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))

        if failures:
            raise CommandError(f"Agent webhook audit failed: {len(failures)} problem(s)")
        self.stdout.write(self.style.SUCCESS("AGENT_WEBHOOK_AUDIT_OK"))
