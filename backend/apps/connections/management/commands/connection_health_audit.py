from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.connections.models import AgentConnectionBinding, ExternalConnection


class Command(BaseCommand):
    help = "Audit tenant isolation and operational health of external agent connections"

    def handle(self, *args, **options):
        failures = []
        warnings = []
        stale_cutoff = timezone.now() - timedelta(days=7)

        self.stdout.write("=== CONNECTION HEALTH AUDIT ===")

        for connection in ExternalConnection.objects.iterator(chunk_size=200):
            if connection.enabled and not connection.secret_encrypted:
                failures.append(f"connection={connection.id}: enabled connection has no encrypted credential")
            if connection.enabled and connection.health_state == ExternalConnection.Health.DISABLED:
                failures.append(f"connection={connection.id}: enabled connection is marked disabled")
            if not connection.enabled and connection.health_state != ExternalConnection.Health.DISABLED:
                warnings.append(f"connection={connection.id}: disabled connection health state is stale")
            if connection.enabled and connection.last_checked_at and connection.last_checked_at < stale_cutoff:
                warnings.append(f"connection={connection.id}: health check is older than 7 days")
            if connection.enabled and connection.health_state == ExternalConnection.Health.DEGRADED:
                warnings.append(f"connection={connection.id}: reconnect/check required")

        for binding in AgentConnectionBinding.objects.select_related("agent", "connection").iterator(chunk_size=500):
            if binding.agent.owner_id != binding.connection.owner_id:
                failures.append(f"binding={binding.id}: cross-tenant connection reference")
            if binding.enabled and not binding.connection.enabled:
                failures.append(f"binding={binding.id}: enabled binding points to disabled connection")

        self.stdout.write(
            f"connections={ExternalConnection.objects.count()} bindings={AgentConnectionBinding.objects.count()}"
        )
        for warning in warnings:
            self.stdout.write(self.style.WARNING(f"[WARN] {warning}"))
        for failure in failures:
            self.stdout.write(self.style.ERROR(f"[FAIL] {failure}"))

        if failures:
            raise CommandError(f"Connection health audit failed: {len(failures)} problem(s)")
        self.stdout.write(self.style.SUCCESS("CONNECTION_HEALTH_AUDIT_OK"))
