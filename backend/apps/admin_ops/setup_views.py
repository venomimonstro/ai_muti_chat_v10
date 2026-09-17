from django.shortcuts import get_object_or_404
from rest_framework.response import Response

from apps.ai_registry.models import Provider

from .commercial_bootstrap import (
    bootstrap_commercial_catalog,
    check_provider_health,
    commercial_setup_status,
)
from .views import AdminAPIView


class CommercialSetupView(AdminAPIView):
    def get(self, request):
        return Response(commercial_setup_status())

    def post(self, request):
        action = request.data.get("action")
        if action != "bootstrap":
            return Response({"detail": "Unsupported action"}, status=400)
        return Response({"created": bootstrap_commercial_catalog(), "status": commercial_setup_status()})


class CommercialProviderHealthView(AdminAPIView):
    def post(self, request, provider_slug):
        provider = get_object_or_404(Provider, slug=provider_slug)
        result = check_provider_health(provider)
        return Response(result, status=200 if result["healthy"] else 424)
