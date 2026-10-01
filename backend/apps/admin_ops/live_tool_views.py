from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from apps.ai_registry import web_tools
from apps.ai_registry.models import Provider, ProviderApiKey
from apps.chat.live_tools import LiveToolError, current_time, current_weather
from apps.procurement.models import ProviderFundingAccount
from apps.procurement.services import (
    create_funding_account,
    record_purchase,
    set_default_account,
)

from .services import audit
from .views import AdminAPIView
from .yandex_search_probe import probe_yandex_search


YANDEX_SEARCH_SLUG = "yandex-search"
YANDEX_SEARCH_NAME = "Yandex Search API"
YANDEX_SEARCH_ENDPOINT = "https://searchapi.api.cloud.yandex.net/v2/web/search"
YANDEX_SEARCH_KEY_LABEL = "Yandex Search API"


def _provider():
    return Provider.objects.filter(slug=YANDEX_SEARCH_SLUG).prefetch_related("api_keys").first()


def _decimal(value, *, default="0"):
    try:
        return Decimal(str(value if value not in {None, ""} else default))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Укажите корректное числовое значение") from exc


def _payload():
    provider = _provider()
    full_status = web_tools.web_search_status()
    status = dict((full_status or {}).get("yandex") or {})
    auth = provider.auth_config if provider else {}
    key = provider.api_keys.filter(enabled=True).order_by("priority", "created_at").first() if provider else None
    return {
        "yandex_search": {
            **status,
            "provider_enabled": bool(provider and provider.enabled and not provider.emergency_disabled),
            "api_key_configured": bool(provider and provider.credential_configured()),
            "api_key_masked": key.masked if key else "",
            "api_key_health": key.health_state if key else ProviderApiKey.HealthState.UNKNOWN,
            "api_key_error": key.last_error_code if key else "",
            "folder_id": str((auth or {}).get("folder_id") or ""),
            "region": str((auth or {}).get("region") or "225"),
            "search_type": str((auth or {}).get("search_type") or "SEARCH_TYPE_RU"),
            "markup_percent": str((auth or {}).get("markup_percent") or status.get("markup_percent") or "100"),
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
        folder_id = str(request.data.get("folder_id") or "").strip()
        region = str(request.data.get("region") or "225").strip()
        search_type = str(request.data.get("search_type") or "SEARCH_TYPE_RU").strip()
        api_key = str(request.data.get("api_key") or "").strip()
        try:
            markup = _decimal(request.data.get("markup_percent"), default="100")
            purchased_requests = _decimal(request.data.get("purchased_requests"))
            purchase_cost_rub = _decimal(request.data.get("purchase_cost_rub"))
        except ValidationError as exc:
            return Response({"detail": exc.messages}, status=400)

        if not folder_id:
            return Response({"detail": "Укажите Folder ID каталога Yandex Cloud"}, status=400)
        if search_type not in {"SEARCH_TYPE_RU", "SEARCH_TYPE_TR", "SEARCH_TYPE_COM"}:
            return Response({"detail": "Некорректный тип поиска"}, status=400)
        if not region.isdigit():
            return Response({"detail": "Region должен быть числовым ID региона Яндекса"}, status=400)
        if markup < 0:
            return Response({"detail": "Наценка не может быть отрицательной"}, status=400)
        if purchased_requests < 0 or purchase_cost_rub < 0:
            return Response({"detail": "Закупка не может содержать отрицательные значения"}, status=400)
        if (purchased_requests > 0) != (purchase_cost_rub > 0):
            return Response(
                {"detail": "Для новой закупки одновременно укажите количество запросов и фактически оплаченную сумму"},
                status=400,
            )

        existing = Provider.objects.filter(slug=YANDEX_SEARCH_SLUG).prefetch_related("api_keys").first()
        existing_key = (
            existing.api_keys.filter(enabled=True).order_by("priority", "created_at").first()
            if existing
            else None
        )
        if not api_key and (existing_key is None or not existing.credential_configured()):
            return Response({"detail": "Вставьте API-ключ Yandex Search"}, status=400)

        provider, _ = Provider.objects.select_for_update().get_or_create(
            slug=YANDEX_SEARCH_SLUG,
            defaults={
                "name": YANDEX_SEARCH_NAME,
                "enabled": True,
                "adapter_type": Provider.AdapterType.ECHO,
                "api_base_url": YANDEX_SEARCH_ENDPOINT,
                "priority": 9999,
                "auth_config": {},
            },
        )
        auth = dict(provider.auth_config or {})
        auth.update(
            {
                "folder_id": folder_id,
                "region": region,
                "search_type": search_type,
                "endpoint": YANDEX_SEARCH_ENDPOINT,
                "markup_percent": str(markup),
            }
        )
        provider.name = YANDEX_SEARCH_NAME
        provider.api_base_url = YANDEX_SEARCH_ENDPOINT
        provider.auth_config = auth
        provider.enabled = True
        provider.emergency_disabled = False
        provider.health_state = Provider.HealthState.UNKNOWN
        provider.save(
            update_fields=[
                "name",
                "api_base_url",
                "auth_config",
                "enabled",
                "emergency_disabled",
                "health_state",
            ]
        )

        if api_key:
            key = provider.api_keys.filter(label=YANDEX_SEARCH_KEY_LABEL).first()
            if key is None:
                key = ProviderApiKey(provider=provider, label=YANDEX_SEARCH_KEY_LABEL, priority=10)
            key.set_secret(api_key)
            key.enabled = True
            key.health_state = ProviderApiKey.HealthState.UNKNOWN
            key.last_error_code = ""
            key.last_checked_at = None
            key.save()
        else:
            key = provider.api_keys.filter(enabled=True).order_by("priority", "created_at").first()

        account = ProviderFundingAccount.objects.filter(provider=provider, api_key=key).first()
        if account is None:
            account = create_funding_account(
                provider=provider,
                label="Yandex Search API",
                api_key=key,
                currency="RUB",
                low_balance_native=10,
                priority=10,
                is_default=True,
                notes="Автоматически создано через экран Yandex Search",
            )
        elif not account.is_default or not account.active:
            account = set_default_account(account)

        purchase = None
        if purchased_requests > 0:
            purchase = record_purchase(
                account=account,
                credit_native=purchased_requests,
                base_cost_rub=purchase_cost_rub,
                created_by=request.user,
                payment_amount=purchase_cost_rub,
                payment_currency="RUB",
                reference="Yandex Search API / admin live-tools",
            )

        audit(
            request,
            "live_tools.yandex_search.save",
            "provider",
            provider.id,
            {
                "folder_id": folder_id,
                "region": region,
                "search_type": search_type,
                "markup_percent": str(markup),
                "purchase_id": str(purchase.id) if purchase else "",
                "purchased_requests": str(purchased_requests) if purchase else "0",
                "purchase_cost_rub": str(purchase_cost_rub) if purchase else "0",
            },
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
                items = probe_yandex_search("официальный сайт Яндекс", limit=3)
                result["yandex"] = {
                    "ok": True,
                    "result_count": len(items),
                    "first": {"title": items[0].title, "url": items[0].url} if items else None,
                    "billing": "1 закупочный search-unit учтён как служебная проверка",
                }
                provider = _provider()
                if provider:
                    provider.health_state = Provider.HealthState.HEALTHY
                    provider.last_checked_at = timezone.now()
                    provider.save(update_fields=["health_state", "last_checked_at"])
            except Exception as exc:
                failed = True
                result["yandex"] = {"ok": False, "error": str(exc)}
                provider = _provider()
                if provider:
                    provider.health_state = Provider.HealthState.DEGRADED
                    provider.last_checked_at = timezone.now()
                    provider.save(update_fields=["health_state", "last_checked_at"])

        audit(request, "live_tools.check", "live_tools", metadata={"kind": kind, "failed": failed})
        return Response({"ok": not failed, "checks": result})
