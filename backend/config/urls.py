from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok"})


def readiness(_request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", health),
    path("api/v1/readiness/", readiness),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.ai_registry.urls")),
    path("api/v1/", include("apps.chat.urls")),
    path("api/v1/", include("apps.billing.urls")),
    path("api/v1/", include("apps.payments.urls")),
    path("api/v1/", include("apps.projects.urls")),
    path("api/v1/", include("apps.files.urls")),
    path("api/v1/", include("apps.workspace_search.urls")),
]
