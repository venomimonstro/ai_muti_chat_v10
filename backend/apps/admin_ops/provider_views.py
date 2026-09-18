import os

from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion

from .services import audit
from .views import AdminAPIView


def _provider_enable_blockers(provider: Provider):
    blockers = []
    if provider.credential_env and not os.getenv(provider.credential_env, "").strip():
        blockers.append(f"Не настроен секрет {provider.credential_env}")
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
