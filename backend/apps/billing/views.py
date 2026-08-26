from decimal import Decimal

from django.db.models import Sum
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_ops.permissions import IsPlatformAdmin

from .models import BillingReconciliationRun, CostAnomaly, RequestCost, Wallet


class WalletView(APIView):
    def get(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        entries = wallet.entries.order_by("-created_at")[:50]
        return Response(
            {
                "available_rub": wallet.available_rub,
                "reserved_rub": wallet.reserved_rub,
                "paid_rub": wallet.paid_rub,
                "promo_rub": wallet.promo_rub,
                "entries": [
                    {
                        "id": e.id,
                        "kind": e.kind,
                        "amount_rub": e.amount_rub,
                        "available_delta_rub": e.available_delta_rub,
                        "reserved_delta_rub": e.reserved_delta_rub,
                        "paid_delta_rub": e.paid_delta_rub,
                        "promo_delta_rub": e.promo_delta_rub,
                        "created_at": e.created_at,
                    }
                    for e in entries
                ],
            }
        )


class FinanceSummaryView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        totals = RequestCost.objects.filter(charged_rub__isnull=False).aggregate(
            revenue=Sum("charged_rub"),
            provider_cost=Sum("provider_cost_rub"),
            gross_profit=Sum("gross_profit_rub"),
        )
        revenue = totals["revenue"] or Decimal("0")
        provider_cost = totals["provider_cost"] or Decimal("0")
        gross_profit = totals["gross_profit"] or revenue - provider_cost
        margin = gross_profit / revenue * 100 if revenue else Decimal("0")
        liability = Wallet.objects.aggregate(
            available=Sum("available_rub"), reserved=Sum("reserved_rub")
        )
        last_run = BillingReconciliationRun.objects.first()
        return Response(
            {
                "usage_revenue_rub": revenue,
                "provider_cost_rub": provider_cost,
                "gross_profit_rub": gross_profit,
                "gross_margin_percent": margin.quantize(Decimal("0.001")),
                "balance_liability_rub": (liability["available"] or 0)
                + (liability["reserved"] or 0),
                "open_anomalies": CostAnomaly.objects.filter(
                    status=CostAnomaly.Status.OPEN
                ).count(),
                "last_reconciliation": (
                    {
                        "id": last_run.id,
                        "status": last_run.status,
                        "discrepancies": last_run.discrepancy_count,
                        "finished_at": last_run.finished_at,
                    }
                    if last_run
                    else None
                ),
            }
        )
