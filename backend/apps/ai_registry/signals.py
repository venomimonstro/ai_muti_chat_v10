from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Provider, ProviderApiKey


@receiver(pre_save, sender=ProviderApiKey)
def detect_credential_replacement(sender, instance, raw=False, update_fields=None, **kwargs):
    instance._credential_changed = False
    if raw or not instance.pk:
        return
    previous = ProviderApiKey.objects.filter(pk=instance.pk).values("secret_encrypted", "enabled").first()
    if previous is None:
        return
    secret_written = update_fields is None or "secret_encrypted" in update_fields
    enabled_written = update_fields is None or "enabled" in update_fields
    instance._credential_changed = bool(
        (secret_written and previous["secret_encrypted"] != instance.secret_encrypted)
        or (enabled_written and not previous["enabled"] and instance.enabled)
    )


def _schedule_health_probe():
    """Best-effort immediate verification without making admin saves depend on Celery."""
    try:
        from apps.admin_ops.tasks import provider_health_watch_task

        with provider_health_watch_task.app.connection_for_write(connect_timeout=2) as broker:
            provider_health_watch_task.apply_async(connection=broker, ignore_result=True, retry=False)
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
    if getattr(instance, "_credential_changed", False):
        ProviderApiKey.objects.filter(pk=instance.pk).update(health_state=ProviderApiKey.HealthState.UNKNOWN)
        instance.health_state = ProviderApiKey.HealthState.UNKNOWN
    if instance.health_state != ProviderApiKey.HealthState.UNKNOWN:
        return
    # An unverified new/repaired key must not take verified sibling keys offline.
    # HEALTHY saves are server-side probe results/metadata and need no new probe.
    if ProviderApiKey.objects.filter(provider_id=instance.provider_id, enabled=True, health_state=ProviderApiKey.HealthState.HEALTHY).exclude(pk=instance.pk).exists():
        transaction.on_commit(_schedule_health_probe)
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
