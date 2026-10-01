from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.ai_registry.models import Provider, ProviderApiKey

from .models import ProviderPurchase
from .services import account_available_native, credential_is_configured


@receiver(post_save, sender=ProviderPurchase)
def reopen_provider_after_purchase(sender, instance, created, raw=False, **kwargs):
    """A real top-up makes a quota-blocked provider eligible for a recovery probe.

    We deliberately set UNKNOWN rather than HEALTHY: funding data cannot prove the
    upstream API works. The next health/runtime request must prove it. The exact API
    key bound to the funded account must be reopened too; otherwise a strict customer
    readiness check can leave the provider UNKNOWN while its credential remains
    DEGRADED forever after a previous quota/auth failure.
    """
    if raw or not created:
        return
    account = instance.account
    if not account.active or account_available_native(account) <= 0:
        return
    if not credential_is_configured(account):
        return
    Provider.objects.filter(
        pk=account.provider_id,
        enabled=True,
        emergency_disabled=False,
    ).update(
        health_state=Provider.HealthState.UNKNOWN,
        consecutive_failures=0,
        circuit_opened_until=None,
    )
    if account.api_key_id:
        ProviderApiKey.objects.filter(
            pk=account.api_key_id,
            provider_id=account.provider_id,
            enabled=True,
        ).update(
            health_state=ProviderApiKey.HealthState.UNKNOWN,
            last_error_code="",
        )
