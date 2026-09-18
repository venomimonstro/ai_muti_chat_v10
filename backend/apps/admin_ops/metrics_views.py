from datetime import timedelta

from django.core.cache import cache
from django.db.models import Avg, Count
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.models import Provider
from apps.chat.models import Generation
from apps.payments.models import Payment, Refund

from .permissions import IsPlatformAdmin


class OperationalMetricsView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        hour = timezone.now() - timedelta(hours=1)
        generations = Generation.objects.filter(created_at__gte=hour)
        generation_stats = generations.aggregate(
            total=Count("id"), average_cost=Avg("actual_cost_rub")
        )
        failed = generations.filter(state=Generation.State.FAILED).count()
        total = generation_stats["total"] or 0
        return Response(
            {
                "http": {
                    "requests_total": cache.get("metric:http_requests_total", 0),
                    "http_5xx_total": cache.get("metric:http_5xx_total", 0),
                    "latency_ms_total": cache.get("metric:http_latency_ms_total", 0),
                },
                "ai_last_hour": {
                    "requests": total,
                    "failed": failed,
                    "error_rate_percent": round(failed / total * 100, 3) if total else 0,
                    "average_cost_rub": generation_stats["average_cost"],
                },
                "providers": [
                    {
                        "slug": provider.slug,
                        "health": provider.health_state,
                        "latency_ms": provider.last_latency_ms,
                        "last_checked_at": provider.last_checked_at,
                    }
                    for provider in Provider.objects.order_by("priority")
                ],
                "payments_last_hour": {
                    "created": Payment.objects.filter(created_at__gte=hour).count(),
                    "failed_or_canceled": Payment.objects.filter(
                        created_at__gte=hour, status=Payment.Status.CANCELED
                    ).count(),
                    "refunds": Refund.objects.filter(created_at__gte=hour).count(),
                },
            }
        )
