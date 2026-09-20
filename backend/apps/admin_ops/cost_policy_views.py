from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from apps.billing.cost_policy import PricingOverheadPolicyVersion, active_pricing_overhead_policy
from apps.billing.models import PriceVersion
from apps.billing.pricing import quote
from apps.ai_registry.models import AIModel

from .services import audit
from .views import AdminAPIView


def _dec(value, label):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{label}: укажите число")
    if result < 0 or result > 100:
        raise ValueError(f"{label}: допустимо от 0 до 100%")
    return result


def _payload(item):
    return {
        "id": str(item.id),
        "tax_percent": str(item.tax_percent),
        "topup_fee_percent": str(item.topup_fee_percent),
        "other_expenses_percent": str(item.other_expenses_percent),
        "refund_withdrawal_percent": str(item.refund_withdrawal_percent),
        "total_percent": str(item.total_percent),
        "effective_from": item.effective_from,
        "reason": item.reason,
    }


def _affected_models():
    result = []
    for model in AIModel.objects.filter(enabled=True).select_related("provider"):
        price = PriceVersion.objects.filter(
            model_slug=model.slug,
            active=True,
            effective_from__lte=timezone.now(),
        ).order_by("-effective_from", "-created_at").first()
        if not price:
            continue
        try:
            iq = quote(price, 1_000_000, 0, provider_slug=model.provider.slug, model_slug=model.slug)
            oq = quote(price, 0, 1_000_000, provider_slug=model.provider.slug, model_slug=model.slug)
        except Exception as exc:
            result.append({"model": model.slug, "error": str(exc)})
            continue
        result.append({
            "model": model.slug,
            "input_margin_percent": str(iq.gross_margin_percent),
            "output_margin_percent": str(oq.gross_margin_percent),
            "margin_allowed": bool(iq.margin_allowed and oq.margin_allowed),
        })
    return result


class PricingOverheadPolicyView(AdminAPIView):
    def get(self, request):
        item = active_pricing_overhead_policy()
        return Response({"policy": _payload(item), "models": _affected_models()})

    @transaction.atomic
    def post(self, request):
        try:
            tax = _dec(request.data.get("tax_percent", 0), "Налог")
            topup = _dec(request.data.get("topup_fee_percent", 0), "Комиссия пополнения")
            other = _dec(request.data.get("other_expenses_percent", 0), "Прочие расходы")
            refund = _dec(request.data.get("refund_withdrawal_percent", 0), "Комиссия возврата/вывода")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        PricingOverheadPolicyVersion.objects.filter(active=True).update(active=False)
        item = PricingOverheadPolicyVersion.objects.create(
            tax_percent=tax,
            topup_fee_percent=topup,
            other_expenses_percent=other,
            refund_withdrawal_percent=refund,
            active=True,
            effective_from=timezone.now(),
            reason=str(request.data.get("reason") or "Изменено владельцем через Экономика AI")[:300],
        )
        affected = _affected_models()
        blocked = [x for x in affected if x.get("margin_allowed") is False or x.get("error")]
        audit(
            request,
            "pricing.overhead_policy_changed",
            "pricing",
            item.id,
            {
                "tax_percent": str(tax),
                "topup_fee_percent": str(topup),
                "other_expenses_percent": str(other),
                "refund_withdrawal_percent": str(refund),
                "total_percent": str(item.total_percent),
                "models_below_floor": len(blocked),
            },
        )
        return Response({"policy": _payload(item), "models": affected, "models_below_floor": blocked})
