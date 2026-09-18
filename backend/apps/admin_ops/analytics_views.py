from datetime import timedelta

from django.db.models import Count
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .analytics_models import ProductEvent
from .permissions import IsPlatformAdmin

ALLOWED_EVENTS = {
    "landing_view",
    "pricing_view",
    "register_start",
    "register_complete",
    "email_verified",
    "workspace_open",
    "first_chat",
    "second_chat",
    "project_created",
    "file_uploaded",
    "payment_started",
    "payment_success",
    "payment_failed",
    "returned_day_1",
    "returned_day_7",
}


class ProductEventIngestView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        event_name = str(request.data.get("event_name", ""))
        event_id = str(request.data.get("client_event_id", ""))[:80]
        if event_name not in ALLOWED_EVENTS or not event_id:
            return Response({"detail": "Invalid event"}, status=400)
        if not request.session.session_key:
            request.session.create()
        metadata = request.data.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = {str(k)[:80]: str(v)[:300] for k, v in list(metadata.items())[:20]}
        ProductEvent.objects.get_or_create(
            client_event_id=event_id,
            defaults={
                "event_name": event_name,
                "user": request.user if request.user.is_authenticated else None,
                "session_key": request.session.session_key or "",
                "source_path": str(request.data.get("source_path", ""))[:240],
                "metadata": metadata,
            },
        )
        return Response(status=204)


class ProductAnalyticsView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        try:
            days = min(max(int(request.query_params.get("days", 30)), 1), 180)
        except (TypeError, ValueError):
            days = 30
        since = timezone.now() - timedelta(days=days)
        queryset = ProductEvent.objects.filter(created_at__gte=since)
        counts = {item["event_name"]: item["count"] for item in queryset.values("event_name").annotate(count=Count("id"))}
        landing = counts.get("landing_view", 0)
        registered = counts.get("register_complete", 0)
        verified = counts.get("email_verified", 0)
        first_chat = counts.get("first_chat", 0)
        payment = counts.get("payment_success", 0)

        def rate(value, base):
            return round(value / base * 100, 2) if base else 0

        return Response(
            {
                "period_days": days,
                "events": counts,
                "funnel": {
                    "landing": landing,
                    "registered": registered,
                    "verified": verified,
                    "first_chat": first_chat,
                    "paid": payment,
                    "rates_percent": {
                        "landing_to_registration": rate(registered, landing),
                        "registration_to_verified": rate(verified, registered),
                        "verified_to_first_chat": rate(first_chat, verified),
                        "first_chat_to_payment": rate(payment, first_chat),
                    },
                },
                "daily": list(
                    queryset.extra(select={"day": "DATE(created_at)"})
                    .values("day", "event_name")
                    .annotate(count=Count("id"))
                    .order_by("day", "event_name")
                ),
            }
        )
