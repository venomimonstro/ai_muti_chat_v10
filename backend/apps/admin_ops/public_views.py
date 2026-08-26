from django.core.cache import cache
from django.db import connection
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.models import Provider

from .models import StatusIncident


class PublicStatusView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        database_status = "operational"
        cache_status = "operational"
        incidents = []
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            database_status = "major_outage"
        try:
            cache.set("status-probe", "ok", timeout=10)
            if cache.get("status-probe") != "ok":
                raise RuntimeError("Cache probe failed")
        except Exception:
            cache_status = "major_outage"
        provider_status = "unknown"
        active_incidents = False
        if database_status == "operational":
            try:
                providers = Provider.objects.filter(enabled=True)
                provider_status = (
                    "partial_outage"
                    if providers.filter(emergency_disabled=True).exists()
                    else "operational"
                )
                active_incidents = StatusIncident.objects.exclude(
                    state=StatusIncident.State.RESOLVED
                ).exists()
                incidents = list(StatusIncident.objects.all()[:20])
            except Exception:
                database_status = "major_outage"
                provider_status = "unknown"
                active_incidents = False
                incidents = []
        overall = (
            "major_outage"
            if database_status == "major_outage" or cache_status == "major_outage"
            else "degraded"
            if provider_status != "operational" or active_incidents
            else "operational"
        )
        return Response(
            {
                "status": overall,
                "components": [
                    {"name": "API", "status": "operational"},
                    {"name": "База данных", "status": database_status},
                    {"name": "Очередь задач", "status": cache_status},
                    {"name": "AI-провайдеры", "status": provider_status},
                ],
                "incidents": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "message": item.message,
                        "impact": item.impact,
                        "state": item.state,
                        "affected_components": item.affected_components,
                        "started_at": item.started_at,
                        "updated_at": item.updated_at,
                        "resolved_at": item.resolved_at,
                    }
                    for item in incidents
                ],
            }
        )
