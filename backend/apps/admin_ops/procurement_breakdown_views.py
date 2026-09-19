from decimal import Decimal

from django.db.models import Count, Sum
from rest_framework.response import Response

from apps.procurement.models import ProviderSpend

from .procurement_views import _range
from .views import AdminAPIView

ZERO = Decimal("0")


def _row(item):
    revenue = item["revenue"] or ZERO
    economic = item["economic"] or ZERO
    nominal = item["nominal"] or ZERO
    profit = revenue - economic
    margin = profit / revenue * Decimal("100") if revenue else ZERO
    total_tokens = (item["input_tokens"] or 0) + (item["output_tokens"] or 0)
    return {
        **item,
        "revenue": str(revenue),
        "economic": str(economic),
        "nominal": str(nominal),
        "profit": str(profit),
        "margin_percent": str(margin.quantize(Decimal("0.001"))),
        "profit_per_million_tokens_rub": (
            str((profit / Decimal(total_tokens) * Decimal("1000000")).quantize(Decimal("0.01")))
            if total_tokens
            else None
        ),
        "native_cost": str(item["native_cost"] or ZERO),
    }


class ProcurementBreakdownView(AdminAPIView):
    def get(self, request):
        try:
            date_from, date_to, start, end = _range(request)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=400)
        qs = ProviderSpend.objects.filter(created_at__gte=start, created_at__lt=end)
        metrics = {
            "requests": Count("id"),
            "revenue": Sum("customer_charge_rub"),
            "economic": Sum("economic_cost_rub"),
            "nominal": Sum("nominal_cost_rub"),
            "native_cost": Sum("native_cost"),
            "input_tokens": Sum("input_tokens"),
            "output_tokens": Sum("output_tokens"),
        }
        by_account = [
            _row(item)
            for item in qs.values(
                "account_id",
                "account__label",
                "account__currency",
                "account__provider__slug",
                "account__provider__name",
            )
            .annotate(**metrics)
            .order_by("account__provider__name", "account__label")
        ]
        by_model = [
            _row(item)
            for item in qs.values(
                "model_slug",
                "account__provider__slug",
                "account__provider__name",
            )
            .annotate(**metrics)
            .order_by("account__provider__name", "model_slug")
        ]
        by_operation = [
            _row(item)
            for item in qs.values("source_type").annotate(**metrics).order_by("source_type")
        ]
        return Response(
            {
                "period": {"from": date_from, "to": date_to},
                "by_account": by_account,
                "by_model": by_model,
                "by_operation": by_operation,
            }
        )
