from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Provider, ProviderApiKey


@receiver(post_save, sender=ProviderApiKey)
def reopen_provider_after_credential_change(sender, instance, created, raw=False, **kwargs):
    """Make a repaired/new credential probeable without blindly declaring it healthy.

    Runtime health updates use QuerySet.update(), so this signal is reserved for
    actual model/admin saves such as adding a key, enabling it or replacing its
    secret. UNKNOWN means the next health/runtime probe must prove recovery.
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
