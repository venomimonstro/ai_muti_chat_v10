from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Generation


MONEY_FIELD = DecimalField(max_digits=14, decimal_places=4)
MONEY_ZERO = Value(Decimal("0.0000"), output_field=MONEY_FIELD)
TERMINAL_STATES = [
    Generation.State.COMPLETED,
    Generation.State.FAILED,
    Generation.State.CANCELLED,
]


def public_model_name(value):
    raw = str(value or "")
    lowered = raw.casefold()
    if lowered.startswith("gigachat"):
        if "lite" in lowered or lowered in {"gigachat-2", "gigachat"}:
            return "System Lite"
        if "max" in lowered:
            return "System Max"
        return "System Pro"
    return raw


class UsageSummaryView(APIView):
    def get(self, request):
        now = timezone.now()
        local_now = timezone.localtime(now)
        today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        since_30 = now - timedelta(days=30)
        # A cancelled/failed generation can still have a confirmed partial charge
        # after tokens were delivered. Excluding it would make the usage screen
        # disagree with the wallet ledger, so all terminal generations belong here.
        qs = Generation.objects.filter(
            owner=request.user,
            state__in=TERMINAL_STATES,
            completed_at__gte=since_30,
        )

        def period_from(cutoff):
            row = qs.filter(completed_at__gte=cutoff).aggregate(
                requests=Count("id"),
                cost=Coalesce(Sum("actual_cost_rub"), MONEY_ZERO, output_field=MONEY_FIELD),
                input_tokens=Coalesce(Sum("input_tokens"), 0),
                output_tokens=Coalesce(Sum("output_tokens"), 0),
            )
            return {
                "requests": row["requests"],
                "cost_rub": str(row["cost"]),
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
            }

        raw_by_model = list(
            qs.values("routed_model")
            .annotate(
                requests=Count("id"),
                cost_rub=Coalesce(Sum("actual_cost_rub"), MONEY_ZERO, output_field=MONEY_FIELD),
                input_tokens=Coalesce(Sum("input_tokens"), 0),
                output_tokens=Coalesce(Sum("output_tokens"), 0),
            )
            .order_by("-cost_rub")[:20]
        )
        # Never leak the internal GigaChat provider/model vocabulary through the
        # customer usage page. Merge rows if legacy/current slugs map to one tier.
        merged = {}
        for row in raw_by_model:
            name = public_model_name(row.get("routed_model"))
            target = merged.setdefault(
                name,
                {"routed_model": name, "requests": 0, "cost_rub": Decimal("0"), "input_tokens": 0, "output_tokens": 0},
            )
            target["requests"] += row["requests"]
            target["cost_rub"] += row["cost_rub"]
            target["input_tokens"] += row["input_tokens"]
            target["output_tokens"] += row["output_tokens"]
        by_model = sorted(merged.values(), key=lambda item: item["cost_rub"], reverse=True)[:20]

        daily = list(
            qs.annotate(day=TruncDate("completed_at"))
            .values("day")
            .annotate(
                requests=Count("id"),
                cost_rub=Coalesce(Sum("actual_cost_rub"), MONEY_ZERO, output_field=MONEY_FIELD),
                input_tokens=Coalesce(Sum("input_tokens"), 0),
                output_tokens=Coalesce(Sum("output_tokens"), 0),
            )
            .order_by("day")
        )
        for row in by_model:
            row["cost_rub"] = str(row["cost_rub"])
        for row in daily:
            row["day"] = row["day"].isoformat() if row["day"] else None
            row["cost_rub"] = str(row["cost_rub"])
        return Response(
            {
                "today": period_from(today_start),
                "seven_days": period_from(now - timedelta(days=7)),
                "thirty_days": period_from(since_30),
                "by_model": by_model,
                "daily": daily,
            }
        )
