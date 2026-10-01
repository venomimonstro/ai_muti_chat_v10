from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from apps.ai_registry.models import Provider, ProviderApiKey
from apps.procurement.models import ProviderFundingAccount

from .procurement_ledger_views import ProcurementLedgerView
from .provider_key_views import _wake_provider_recovery
from .provider_views import ProviderKeyDetailView
from .services import audit


class OwnerProviderKeyDetailView(ProviderKeyDetailView):
    """Owner delete that removes the secret while retaining anonymized accounting history."""

    @transaction.atomic
    def delete(self, request, provider_slug, key_id):
        item = get_object_or_404(
            ProviderApiKey.objects.select_for_update().select_related("provider"),
            id=key_id,
            provider__slug=provider_slug,
        )
        provider = item.provider
        accounts = list(ProviderFundingAccount.objects.select_for_update().filter(api_key=item))
        for account in accounts:
            note = (account.notes or "").strip()
            suffix = f"API-ключ {item.label} удалён владельцем {timezone.now().isoformat()}; финансовая история сохранена."
            ProviderFundingAccount.objects.filter(pk=account.pk).update(
                api_key=None,
                credential_env="",
                active=False,
                is_default=False,
                notes=(f"{note}\n{suffix}" if note else suffix)[:4000],
                updated_at=timezone.now(),
            )
        item.delete()
        has_healthy = provider.api_keys.filter(
            enabled=True,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        ).exists()

        # Credential maintenance must never promote an unhealthy provider to
        # customer-ready. If no verified credential remains, move to UNKNOWN. If a
        # healthy spare remains, preserve the current provider circuit state and let
        # the background health + inference probe be the only recovery authority.
        if not has_healthy:
            provider.health_state = Provider.HealthState.UNKNOWN
            provider.save(update_fields=["health_state"])
        elif provider.health_state != Provider.HealthState.HEALTHY:
            if provider.enabled and provider.models.filter(enabled=True).exists():
                transaction.on_commit(_wake_provider_recovery)

        audit(
            request,
            "provider.key.owner_deleted",
            "provider",
            provider.id,
            metadata={
                "provider": provider.slug,
                "key_id": str(key_id),
                "detached_accounts": len(accounts),
                "provider_health": provider.health_state,
                "healthy_spare_remaining": has_healthy,
            },
        )
        return Response(status=204)


class OwnerProcurementLedgerView(ProcurementLedgerView):
    """Owner procurement ledger with the same immutable FIFO protections as the base ledger."""

    # Do not special-case consumed purchase deletion here. The base implementation
    # rejects edit/cancel/delete once ProviderSpendAllocation exists, preserving
    # the purchase document and its FIFO/economic audit trail.
    pass
