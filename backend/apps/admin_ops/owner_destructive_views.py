from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from apps.ai_registry.models import Provider, ProviderApiKey
from apps.procurement.models import ProviderFundingAccount, ProviderPurchase, ProviderSpendAllocation

from .procurement_ledger_views import ProcurementLedgerView, _purchase_payload, _unfund_order
from .provider_views import ProviderKeyDetailView
from .services import audit


ZERO = Decimal("0")


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
        has_healthy = provider.api_keys.filter(enabled=True, health_state=ProviderApiKey.HealthState.HEALTHY).exists()
        provider.health_state = Provider.HealthState.HEALTHY if has_healthy else Provider.HealthState.UNKNOWN
        provider.last_checked_at = timezone.now()
        provider.save(update_fields=["health_state", "last_checked_at"])
        audit(
            request,
            "provider.key.owner_deleted",
            "provider",
            provider.id,
            metadata={"provider": provider.slug, "key_id": str(key_id), "detached_accounts": len(accounts)},
        )
        return Response(status=204)


class OwnerProcurementLedgerView(ProcurementLedgerView):
    """Lets the owner remove purchase documents without corrupting immutable spend allocations."""

    def get(self, request):
        response = super().get(request)
        if isinstance(response.data, dict):
            for row in response.data.get("purchases", []):
                row["deletable"] = row.get("state") != ProviderPurchase.State.DELETED
                if row.get("operations_count", 0):
                    row["delete_mode"] = "archive"
                    row["delete_hint"] = "Неиспользованный остаток будет снят; использованная FIFO-история сохранится для корректного учёта."
                else:
                    row["delete_mode"] = "delete"
        return response

    @transaction.atomic
    def post(self, request):
        action = str(request.data.get("action") or "purchase_key").strip()
        if action != "delete_purchase":
            return super().post(request)

        purchase = get_object_or_404(
            ProviderPurchase.objects.select_for_update().select_related("account"),
            pk=request.data.get("purchase_id"),
        )
        if purchase.state == ProviderPurchase.State.DELETED:
            return Response({"detail": "Закупочный ордер уже удалён"}, status=400)
        account = ProviderFundingAccount.objects.select_for_update().get(pk=purchase.account_id)
        allocated = ProviderSpendAllocation.objects.filter(purchase=purchase).aggregate(value=Sum("native_amount"))["value"] or ZERO
        has_allocations = allocated > ZERO
        now = timezone.now()

        if purchase.state == ProviderPurchase.State.ACTIVE:
            if not has_allocations:
                _unfund_order(purchase, account)
            else:
                # Preserve the already-consumed lot in immutable FIFO history, but
                # remove the unconsumed credit from the operational funding balance.
                unused = max(ZERO, purchase.credit_native - allocated)
                new_funded = account.funded_native - unused
                required = account.spent_native + account.reserved_native
                if new_funded < required:
                    return Response({"detail": "Нельзя удалить пополнение: его неиспользованный остаток уже нужен активному резерву."}, status=409)
                account.funded_native = new_funded
                account.save(update_fields=["funded_native", "updated_at"])

        ProviderPurchase.objects.filter(pk=purchase.pk).update(
            state=ProviderPurchase.State.DELETED,
            deleted_at=now,
            updated_at=now,
        )
        purchase.refresh_from_db()
        audit(
            request,
            "procurement.purchase_owner_deleted",
            "provider_purchase",
            str(purchase.id),
            {
                "document_number": purchase.document_number,
                "preserved_fifo_history": has_allocations,
                "allocated_native": str(allocated),
            },
        )
        payload = _purchase_payload(purchase)
        payload["archived_history_preserved"] = has_allocations
        return Response(payload)
