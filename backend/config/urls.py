from django.contrib import admin
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path

from apps.admin_ops.public_views import PublicStatusView


def health(_request):
    return JsonResponse({"status": "ok"})


def readiness(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        cache.set("readiness-probe", "ready", timeout=10)
        if cache.get("readiness-probe") != "ready":
            raise RuntimeError("Cache readiness check failed")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", health),
    path("api/v1/readiness/", readiness),
    path("api/v1/status/", PublicStatusView.as_view(), name="public-status"),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.ai_registry.urls")),
    path("api/v1/", include("apps.chat.urls")),
    path("api/v1/", include("apps.billing.urls")),
    path("api/v1/", include("apps.payments.urls")),
    path("api/v1/", include("apps.projects.urls")),
    path("api/v1/", include("apps.files.urls")),
    path("api/v1/", include("apps.workspace_search.urls")),
    path("api/v1/", include("apps.memory_store.urls")),
    path("api/v1/", include("apps.evals.urls")),
    path("api/v1/", include("apps.image_studio.urls")),
    path("api/v1/", include("apps.b2b_api.management_urls")),
    path("api/v1/admin/", include("apps.admin_ops.urls")),
    path("v1/", include("apps.b2b_api.public_urls")),
]
