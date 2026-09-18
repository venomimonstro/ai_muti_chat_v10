from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Generation


class UsageSummaryView(APIView):
    def get(self, request):
        now = timezone.now()
        since_30 = now - timedelta(days=30)
        qs = Generation.objects.filter(
            owner=request.user,
            state=Generation.State.COMPLETED,
            completed_at__gte=since_30,
        )

        def period(days):
            cutoff = now - timedelta(days=days)
            row = qs.filter(completed_at__gte=cutoff).aggregate(
                requests=Count("id"),
                cost=Coalesce(Sum("actual_cost_rub"), 0),
                input_tokens=Coalesce(Sum("input_tokens"), 0),
                output_tokens=Coalesce(Sum("output_tokens"), 0),
            )
            return {
                "requests": row["requests"],
                "cost_rub": str(row["cost"]),
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
            }

        by_model = list(
            qs.values("routed_model")
            .annotate(
                requests=Count("id"),
                cost_rub=Coalesce(Sum("actual_cost_rub"), 0),
                input_tokens=Coalesce(Sum("input_tokens"), 0),
                output_tokens=Coalesce(Sum("output_tokens"), 0),
            )
            .order_by("-cost_rub")[:20]
        )
        daily = list(
            qs.annotate(day=TruncDate("completed_at"))
            .values("day")
            .annotate(
                requests=Count("id"),
                cost_rub=Coalesce(Sum("actual_cost_rub"), 0),
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
                "today": period(1),
                "seven_days": period(7),
                "thirty_days": period(30),
                "by_model": by_model,
                "daily": daily,
            }
        )
