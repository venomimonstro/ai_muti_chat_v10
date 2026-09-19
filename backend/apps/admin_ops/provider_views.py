import os

from django.core.validators import URLValidator
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion

from .services import audit
from .views import AdminAPIView


def _provider_enable_blockers(provider: Provider):
    blockers = []
    if not provider.credential_configured() and provider.adapter_type != Provider.AdapterType.ECHO:
        blockers.append("Не настроен API-ключ провайдера")
    if provider.health_state != Provider.HealthState.HEALTHY:
        blockers.append("Провайдер должен успешно пройти проверку связи")
    return blockers


def _model_enable_blockers(model: AIModel):
    blockers = []
    if not model.provider.enabled or model.provider.emergency_disabled:
        blockers.append("Провайдер модели выключен")
    blockers.extend(_provider_enable_blockers(model.provider))
    if not model.upstream_model.strip():
        blockers.append("Не указан ID модели у провайдера")
    if not model.current_version_id:
        blockers.append("Не назначена активная версия модели")
    now = timezone.now()
    price = (
        PriceVersion.objects.filter(
            model_slug=model.slug,
            active=True,
            effective_from__lte=now,
            input_rub_per_million__gt=0,
            output_rub_per_million__gt=0,
        )
        .order_by("-effective_from", "-created_at")
        .first()
    )
    if price is None:
        blockers.append("Не настроена действующая положительная цена входных и выходных токенов")
    return list(dict.fromkeys(blockers))


def _credential_payload(provider: Provider):
    return {
        "id": str(provider.id),
        "slug": provider.slug,
        "name": provider.name,
        "adapter_type": provider.adapter_type,
        "api_base_url": provider.api_base_url,
        "credential_env": provider.credential_env,
        "credential_configured": provider.credential_configured(),
        "credential_source": provider.credential_source(),
        "health_state": provider.health_state,
        "last_checked_at": provider.last_checked_at,
        "last_latency_ms": provider.last_latency_ms,
    }


class ProviderCredentialView(AdminAPIView):
    def get(self, request, provider_slug):
        provider = get_object_or_404(Provider, slug=provider_slug)
        return Response(_credential_payload(provider))

    @transaction.atomic
    def patch(self, request, provider_slug):
        provider = get_object_or_404(Provider.objects.select_for_update(), slug=provider_slug)
        api_key = request.data.get("api_key")
        clear_api_key = request.data.get("clear_api_key") is True
        base_url_supplied = "api_base_url" in request.data

        if clear_api_key and api_key:
            return Response({"detail": "Нельзя одновременно задать и удалить API-ключ"}, status=400)

        if api_key is not None:
            api_key = str(api_key).strip()
            if api_key:
                provider.set_api_key(api_key)
            elif not clear_api_key:
                api_key = None  # Пустое поле означает «оставить текущий ключ».

        if clear_api_key:
            provider.clear_api_key()

        if base_url_supplied:
            base_url = str(request.data.get("api_base_url") or "").strip()
            if base_url:
                try:
                    URLValidator(schemes=["https", "http"])(base_url)
                except Exception:
                    return Response({"detail": "Некорректный URL API"}, status=400)
            provider.api_base_url = base_url

        if api_key or clear_api_key or base_url_supplied:
            provider.health_state = Provider.HealthState.UNKNOWN
            provider.last_checked_at = None
            provider.last_latency_ms = None
            provider.consecutive_failures = 0
            provider.circuit_opened_until = None
            provider.save()
            audit(
                request,
                "provider.credentials.updated",
                "provider",
                provider.id,
                metadata={
                    "provider": provider.slug,
                    "credential_changed": bool(api_key or clear_api_key),
                    "base_url_changed": base_url_supplied,
                    "credential_source": provider.credential_source(),
                },
            )

        return Response(_credential_payload(provider))


class SafeProviderBulkActionView(AdminAPIView):
    @transaction.atomic
    def post(self, request):
        target = request.data.get("target")
        action = request.data.get("action")
        ids = request.data.get("ids")
        if target not in {"providers", "models"} or not isinstance(ids, list) or not ids:
            return Response({"detail": "Укажите объект и непустой список ID"}, status=400)

        if target == "models":
            if action not in {"enable", "disable"}:
                return Response({"detail": "Недопустимое действие с моделями"}, status=400)
            queryset = AIModel.objects.select_related("provider", "current_version").filter(id__in=ids)
            models = list(queryset)
            if len(models) != len(set(ids)):
                return Response({"detail": "Одна или несколько моделей не найдены"}, status=404)
            if action == "enable":
                blocked = {
                    model.slug: _model_enable_blockers(model)
                    for model in models
                    if _model_enable_blockers(model)
                }
                if blocked:
                    return Response(
                        {"detail": "Модель нельзя включить до завершения настройки", "blockers": blocked},
                        status=409,
                    )
            count = queryset.update(enabled=action == "enable")
        else:
            queryset = Provider.objects.filter(id__in=ids)
            providers = list(queryset)
            if len(providers) != len(set(ids)):
                return Response({"detail": "Один или несколько провайдеров не найдены"}, status=404)
            if action in {"enable", "disable"}:
                if action == "enable":
                    blocked = {
                        provider.slug: _provider_enable_blockers(provider)
                        for provider in providers
                        if _provider_enable_blockers(provider)
                    }
                    if blocked:
                        return Response(
                            {"detail": "Провайдера нельзя включить до успешной настройки", "blockers": blocked},
                            status=409,
                        )
                count = queryset.update(enabled=action == "enable")
            elif action in {"emergency_disable", "emergency_enable"}:
                if action == "emergency_enable":
                    blocked = {
                        provider.slug: _provider_enable_blockers(provider)
                        for provider in providers
                        if _provider_enable_blockers(provider)
                    }
                    if blocked:
                        return Response(
                            {"detail": "Нельзя снять аварийное отключение", "blockers": blocked},
                            status=409,
                        )
                count = queryset.update(emergency_disabled=action == "emergency_disable")
            else:
                return Response({"detail": "Недопустимое действие с провайдером"}, status=400)

        audit(request, f"{target}.{action}", target, metadata={"ids": ids, "count": count})
        return Response({"updated": count})
