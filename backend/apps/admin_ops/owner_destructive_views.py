from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from apps.ai_registry.models import Provider, ProviderApiKey
from apps.procurement.models import ProviderFundingAccount, ProviderPurchase, ProviderSpendAllocation

from .procurement_ledger_views import ProcurementLedgerView, _purchase_payload, _unfund_order
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
    """Lets the owner archive purchase documents without corrupting immutable spend allocations."""

    def get(self, request):
        response = super().get(request)
        if isinstance(response.data, dict):
            for row in response.data.get("purchases", []):
                row["deletable"] = row.get("state") != ProviderPurchase.State.DELETED
                if row.get("operations_count", 0):
                    row["delete_mode"] = "archive"
                    row["delete_hint"] = "Будет убран из рабочего списка; использованная история FIFO останется для корректного учёта."
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
        has_allocations = ProviderSpendAllocation.objects.filter(purchase=purchase).exists()
        now = timezone.now()

        if not has_allocations and purchase.state == ProviderPurchase.State.ACTIVE:
            _unfund_order(purchase, account)
        # If the lot has already participated in FIFO, its immutable allocations and
        # funded amount remain part of accounting. We only archive the document from
        # the active admin register; deleting those facts would corrupt realized cost.
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
            {"document_number": purchase.document_number, "preserved_fifo_history": has_allocations},
        )
        payload = _purchase_payload(purchase)
        payload["archived_history_preserved"] = has_allocations
        return Response(payload)
