from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Provider, ProviderApiKey


def _schedule_health_probe():
    """Best-effort immediate verification without making admin saves depend on Celery."""
    try:
        from apps.admin_ops.tasks import provider_health_watch_task

        provider_health_watch_task.delay()
    except Exception:
        # The minute heartbeat remains the durable fallback if broker dispatch is
        # temporarily unavailable. Saving a credential must never return HTTP 500.
        return


@receiver(post_save, sender=ProviderApiKey)
def reopen_provider_after_credential_change(sender, instance, created, raw=False, **kwargs):
    """Make a repaired/new credential probeable without declaring it healthy.

    Customer traffic stays fail-closed while the provider is UNKNOWN. A health
    sweep is queued after the database transaction commits; the periodic heartbeat
    remains a fallback and will retry automatically.
    """
    if raw or not instance.enabled:
        return
    if instance.health_state not in {
        ProviderApiKey.HealthState.UNKNOWN,
        ProviderApiKey.HealthState.HEALTHY,
    }:
        return
    Provider.objects.filter(
        pk=instance.provider_id,
        enabled=True,
        emergency_disabled=False,
    ).update(
        health_state=Provider.HealthState.UNKNOWN,
        consecutive_failures=0,
        circuit_opened_until=None,
    )
    transaction.on_commit(_schedule_health_probe)
