from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from apps.ai_registry.models import Provider, ProviderApiKey
from apps.procurement.models import ProviderFundingAccount
from apps.procurement.services import account_available_native

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
    """Owner procurement ledger plus fail-closed provider recovery triggers.

    Funding mutations must never make a provider customer-ready by themselves. They
    only make a recovery attempt possible; the provider returns to HEALTHY after the
    regular background health + tiny inference proof succeeds.
    """

    @transaction.atomic
    def post(self, request):
        action = str(request.data.get("action") or "purchase_key").strip()
        provider_id = None
        default_changed = False

        if action == "set_default":
            account = (
                ProviderFundingAccount.objects.select_related("provider")
                .filter(pk=request.data.get("account_id"))
                .first()
            )
            if account is not None:
                provider_id = account.provider_id
                current_default = (
                    ProviderFundingAccount.objects.filter(
                        provider_id=provider_id,
                        is_default=True,
                    )
                    .values_list("id", flat=True)
                    .first()
                )
                default_changed = current_default != account.id
        elif action == "purchase_key":
            key = (
                ProviderApiKey.objects.select_related("provider")
                .filter(pk=request.data.get("api_key_id"))
                .first()
            )
            if key is not None:
                provider_id = key.provider_id

        response = super().post(request)
        if response.status_code >= 400 or provider_id is None:
            return response

        provider = Provider.objects.select_for_update().get(pk=provider_id)
        if provider.emergency_disabled:
            # Emergency breaker is owner-controlled and must never be cleared by a
            # purchase/default-account change.
            return response

        should_probe = False
        if action == "set_default" and default_changed:
            # The authoritative credential changed. Even a previously HEALTHY
            # provider must prove inference with the newly selected account before
            # customer traffic can use it.
            if provider.health_state != Provider.HealthState.UNKNOWN:
                provider.health_state = Provider.HealthState.UNKNOWN
                provider.save(update_fields=["health_state"])
            should_probe = True
        elif action == "purchase_key" and provider.health_state != Provider.HealthState.HEALTHY:
            # Only funding of the authoritative/default account can resolve a
            # procurement outage. Funding a spare account must not disturb routing.
            account_id = None
            if isinstance(getattr(response, "data", None), dict):
                account_id = response.data.get("account_id")
            funded_account = (
                ProviderFundingAccount.objects.filter(pk=account_id).first()
                if account_id
                else None
            )
            if (
                funded_account is not None
                and funded_account.is_default
                and account_available_native(funded_account) > 0
            ):
                provider.health_state = Provider.HealthState.UNKNOWN
                provider.save(update_fields=["health_state"])
                should_probe = True

        if (
            should_probe
            and provider.enabled
            and provider.models.filter(enabled=True).exists()
        ):
            transaction.on_commit(_wake_provider_recovery)
        return response
