from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from apps.b2b_api.models import APIUsage
from apps.billing.models import BalanceReservation, RequestCost
from apps.chat.models import CompareVariant
from apps.image_studio.models import ImageGeneration
from apps.procurement.models import ProviderSpend

ZERO = Decimal("0")


def _sum(queryset, field):
    return queryset.aggregate(value=Sum(field))["value"] or ZERO


def install(procurement_views_module) -> None:
    view_class = procurement_views_module.ProcurementEconomicsView
    raw_get = view_class.get
    if getattr(raw_get, "_ai_workspace_search_economics", False):
        return

    def get(self, request):
        response = raw_get(self, request)
        if getattr(response, "status_code", 200) >= 400 or not isinstance(response.data, dict):
            return response
        try:
            _date_from, _date_to, start, end = procurement_views_module._range(request)
            search_charges = BalanceReservation.objects.filter(
                state=BalanceReservation.State.SETTLED,
                idempotency_key__startswith="web-search:",
                created_at__gte=start,
                created_at__lt=end,
                actual_rub__isnull=False,
            )
            search_spends = ProviderSpend.objects.filter(
                source_type="web_search",
                created_at__gte=start,
                created_at__lt=end,
            )
            search_revenue = _sum(search_charges, "actual_rub")
            search_nominal_cost = _sum(search_spends, "nominal_cost_rub")

            summary = dict(response.data.get("summary") or {})
            old_revenue = Decimal(str(summary.get("revenue_rub") or "0"))
            old_nominal = Decimal(str(summary.get("nominal_provider_cost_rub") or "0"))
            recognized = Decimal(str(summary.get("recognized_procurement_cost_rub") or "0"))
            revenue = old_revenue + search_revenue
            nominal = old_nominal + search_nominal_cost
            nominal_profit = revenue - nominal
            economic_profit = revenue - recognized
            summary.update(
                {
                    "revenue_rub": str(revenue),
                    "nominal_provider_cost_rub": str(nominal),
                    "gross_profit_nominal_rub": str(nominal_profit),
                    "gross_margin_nominal_percent": str(
                        ((nominal_profit / revenue * 100) if revenue else ZERO).quantize(Decimal("0.001"))
                    ),
                    "gross_profit_economic_rub": str(economic_profit),
                    "gross_margin_economic_percent": str(
                        ((economic_profit / revenue * 100) if revenue else ZERO).quantize(Decimal("0.001"))
                    ),
                    "web_search_revenue_rub": str(search_revenue),
                    "web_search_nominal_cost_rub": str(search_nominal_cost),
                    "web_search_paid_calls": search_spends.count(),
                }
            )
            response.data["summary"] = summary

            chat_count = RequestCost.objects.filter(
                created_at__gte=start,
                created_at__lt=end,
                charged_rub__isnull=False,
            ).count()
            b2b_count = APIUsage.objects.filter(
                created_at__gte=start,
                created_at__lt=end,
                state=APIUsage.State.COMPLETED,
            ).count()
            image_count = ImageGeneration.objects.filter(
                created_at__gte=start,
                created_at__lt=end,
                state=ImageGeneration.State.COMPLETED,
            ).count()
            compare_count = CompareVariant.objects.filter(
                created_at__gte=start,
                created_at__lt=end,
                state=CompareVariant.State.COMPLETED,
            ).count()
            source_count = chat_count + b2b_count + image_count + compare_count + search_spends.count()
            spend_count = ProviderSpend.objects.filter(created_at__gte=start, created_at__lt=end).count()
            coverage = Decimal(spend_count) / Decimal(source_count) * 100 if source_count else Decimal("100")
            risk = dict(response.data.get("risk") or {})
            risk["unallocated_completed_operations"] = max(0, source_count - spend_count)
            summary["allocation_coverage_percent"] = str(coverage.quantize(Decimal("0.01")))
            response.data["risk"] = risk
        except Exception:
            # Economics enrichment must not make the owner dashboard unavailable.
            # The base report remains authoritative if the optional search slice fails.
            return response
        return response

    get._ai_workspace_search_economics = True
    view_class.get = get
