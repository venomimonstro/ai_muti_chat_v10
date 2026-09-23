from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from apps.ai_registry.models import Provider, ProviderApiKey
from apps.ai_registry.web_tools import WebToolError, search_web, yandex_search_status
from apps.chat.live_tools import LiveToolError, current_time, current_weather

from .services import audit
from .views import AdminAPIView


YANDEX_SEARCH_SLUG = "yandex-search"
YANDEX_SEARCH_NAME = "Yandex Search API"
YANDEX_SEARCH_ENDPOINT = "https://searchapi.api.cloud.yandex.net/v2/web/search"
YANDEX_SEARCH_KEY_LABEL = "Yandex Search API"


def _provider():
    return Provider.objects.filter(slug=YANDEX_SEARCH_SLUG).prefetch_related("api_keys").first()


def _payload():
    provider = _provider()
    status = yandex_search_status()
    auth = provider.auth_config if provider else {}
    key = provider.api_keys.filter(enabled=True).order_by("priority", "created_at").first() if provider else None
    return {
        "yandex_search": {
            **status,
            "api_key_configured": bool(provider and provider.credential_configured()),
            "api_key_masked": key.masked if key else "",
            "folder_id": str((auth or {}).get("folder_id") or ""),
            "region": str((auth or {}).get("region") or "225"),
            "search_type": str((auth or {}).get("search_type") or "SEARCH_TYPE_RU"),
            "last_checked_at": provider.last_checked_at if provider else None,
            "health_state": provider.health_state if provider else Provider.HealthState.UNKNOWN,
        },
        "clock": {"configured": True, "source": "server"},
        "weather": {"configured": True, "source": "Open-Meteo"},
    }


class LiveToolSettingsView(AdminAPIView):
    def get(self, request):
        return Response(_payload())

    @transaction.atomic
    def patch(self, request):
        provider, _ = Provider.objects.select_for_update().get_or_create(
            slug=YANDEX_SEARCH_SLUG,
            defaults={
                "name": YANDEX_SEARCH_NAME,
                "enabled": False,
                "adapter_type": Provider.AdapterType.ECHO,
                "api_base_url": YANDEX_SEARCH_ENDPOINT,
                "priority": 9999,
                "auth_config": {},
            },
        )
        folder_id = str(request.data.get("folder_id") or "").strip()
        region = str(request.data.get("region") or "225").strip()
        search_type = str(request.data.get("search_type") or "SEARCH_TYPE_RU").strip()
        api_key = str(request.data.get("api_key") or "").strip()

        if not folder_id:
            return Response({"detail": "Укажите Folder ID каталога Yandex Cloud"}, status=400)
        if search_type not in {"SEARCH_TYPE_RU", "SEARCH_TYPE_TR", "SEARCH_TYPE_COM"}:
            return Response({"detail": "Некорректный тип поиска"}, status=400)
        if not region.isdigit():
            return Response({"detail": "Region должен быть числовым ID региона Яндекса"}, status=400)

        auth = dict(provider.auth_config or {})
        auth.update(
            {
                "folder_id": folder_id,
                "region": region,
                "search_type": search_type,
                "endpoint": YANDEX_SEARCH_ENDPOINT,
            }
        )
        provider.name = YANDEX_SEARCH_NAME
        provider.api_base_url = YANDEX_SEARCH_ENDPOINT
        provider.auth_config = auth
        provider.health_state = Provider.HealthState.UNKNOWN
        provider.save(update_fields=["name", "api_base_url", "auth_config", "health_state"])

        if api_key:
            key = provider.api_keys.filter(label=YANDEX_SEARCH_KEY_LABEL).first()
            if key is None:
                key = ProviderApiKey(provider=provider, label=YANDEX_SEARCH_KEY_LABEL, priority=10)
            key.set_secret(api_key)
            key.enabled = True
            key.health_state = ProviderApiKey.HealthState.UNKNOWN
            key.last_error_code = ""
            key.save()
        elif not provider.credential_configured():
            return Response({"detail": "Вставьте API-ключ Yandex Search"}, status=400)

        audit(
            request,
            "live_tools.yandex_search.save",
            "provider",
            provider.id,
            {"folder_id": folder_id, "region": region, "search_type": search_type},
        )
        return Response(_payload())


class LiveToolCheckView(AdminAPIView):
    def post(self, request):
        kind = str(request.data.get("kind") or "all").strip().lower()
        result = {}
        failed = False

        if kind in {"all", "time"}:
            try:
                result["time"] = {"ok": True, "data": current_time("Москва")}
            except LiveToolError as exc:
                failed = True
                result["time"] = {"ok": False, "error": str(exc)}

        if kind in {"all", "weather"}:
            try:
                result["weather"] = {"ok": True, "data": current_weather("Москва")}
            except LiveToolError as exc:
                failed = True
                result["weather"] = {"ok": False, "error": str(exc)}

        if kind in {"all", "yandex"}:
            try:
                items = search_web("официальный сайт Яндекс", limit=3)
                result["yandex"] = {
                    "ok": True,
                    "result_count": len(items),
                    "first": {"title": items[0].title, "url": items[0].url} if items else None,
                }
                provider = _provider()
                if provider:
                    provider.health_state = Provider.HealthState.HEALTHY
                    provider.last_checked_at = timezone.now()
                    provider.save(update_fields=["health_state", "last_checked_at"])
                    provider.api_keys.filter(enabled=True).update(
                        health_state=ProviderApiKey.HealthState.HEALTHY,
                        last_error_code="",
                        last_checked_at=timezone.now(),
                    )
            except WebToolError as exc:
                failed = True
                result["yandex"] = {"ok": False, "error": str(exc)}
                provider = _provider()
                if provider:
                    provider.health_state = Provider.HealthState.DEGRADED
                    provider.last_checked_at = timezone.now()
                    provider.save(update_fields=["health_state", "last_checked_at"])
                    provider.api_keys.filter(enabled=True).update(
                        health_state=ProviderApiKey.HealthState.DEGRADED,
                        last_error_code=str(exc)[:80],
                        last_checked_at=timezone.now(),
                    )

        audit(request, "live_tools.check", "live_tools", metadata={"kind": kind, "failed": failed})
        return Response({"ok": not failed, "checks": result}, status=200 if not failed else 503)
