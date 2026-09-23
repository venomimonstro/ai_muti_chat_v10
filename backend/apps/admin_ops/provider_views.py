import os
import time
from decimal import Decimal

import httpx
from django.core.validators import URLValidator
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.response import Response

from apps.ai_registry.gigachat_adapter import GigaChatAPIAdapter
from apps.ai_registry.models import AIModel, ModelVersion, Provider, ProviderApiKey
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
        blockers.append("Не указана модель провайдера")
    if not model.current_version_id:
        blockers.append("Не назначена активная версия модели")
    price = PriceVersion.objects.filter(
        model_slug=model.slug,
        active=True,
        effective_from__lte=timezone.now(),
        input_rub_per_million__gt=0,
        output_rub_per_million__gt=0,
    ).order_by("-effective_from", "-created_at").first()
    if price is None:
        blockers.append("Не настроена стоимость модели")
    return list(dict.fromkeys(blockers))


def _key_payload(item: ProviderApiKey):
    return {
        "id": str(item.id),
        "label": item.label,
        "masked": item.masked,
        "enabled": item.enabled,
        "priority": item.priority,
        "health_state": item.health_state,
        "last_error_code": item.last_error_code,
        "last_latency_ms": item.last_latency_ms,
        "balance_supported": item.balance_supported,
        "balance_amount": str(item.balance_amount) if item.balance_amount is not None else None,
        "balance_currency": item.balance_currency,
        "balance_checked_at": item.balance_checked_at,
        "last_checked_at": item.last_checked_at,
    }


def _credential_payload(provider: Provider):
    return {
        "id": str(provider.id),
        "slug": provider.slug,
        "name": provider.name,
        "adapter_type": provider.adapter_type,
        "api_base_url": provider.api_base_url,
        "credential_configured": provider.credential_configured(),
        "credential_source": provider.credential_source(),
        "health_state": provider.health_state,
        "last_checked_at": provider.last_checked_at,
        "last_latency_ms": provider.last_latency_ms,
        "keys": [_key_payload(item) for item in provider.api_keys.all()],
    }


def _headers(provider: Provider, api_key: str):
    if provider.adapter_type == Provider.AdapterType.ANTHROPIC_MESSAGES:
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    if provider.adapter_type == Provider.AdapterType.GEMINI_GENERATE_CONTENT:
        return {"x-goog-api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


def _models_url(provider: Provider):
    return f"{provider.api_base_url.rstrip('/')}/models"


def _openrouter_key_url(provider: Provider):
    return f"{provider.api_base_url.rstrip('/')}/key"


def _gigachat_adapter(provider: Provider, secret: str):
    return GigaChatAPIAdapter(
        authorization_key=secret,
        base_url=provider.api_base_url or "https://api.giga.chat/v1",
        scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
    )


def _extract_models(provider: Provider, payload):
    raw = payload.get("models", []) if provider.adapter_type == Provider.AdapterType.GEMINI_GENERATE_CONTENT else payload.get("data", [])
    result = []
    for item in raw:
        model_id = str(item.get("name") or item.get("id") or "").strip()
        if model_id.startswith("models/"):
            model_id = model_id[7:]
        if not model_id:
            continue
        result.append({"id": model_id, "display_name": item.get("displayName") or item.get("display_name") or model_id})
    return result


def _purpose(model_id: str):
    value = model_id.lower()
    if any(x in value for x in ("reason", "o1", "o3", "r1")):
        return "Сложные рассуждения, математика, аналитика и трудные задачи"
    if any(x in value for x in ("code", "coder")):
        return "Программирование, исправление кода и разработка"
    if any(x in value for x in ("mini", "nano", "haiku", "flash", "lite")):
        return "Быстрые и недорогие повседневные запросы"
    if any(x in value for x in ("vision", "image", "multimodal")):
        return "Работа с текстом и изображениями"
    if any(x in value for x in ("opus", "pro", "max", "ultra", "gpt-5", "grok-4")):
        return "Максимальное качество для сложных задач"
    return "Универсальный чат, тексты, анализ и рабочие задачи"


def _price_for(provider: Provider, model_id: str):
    model = AIModel.objects.filter(provider=provider, upstream_model=model_id).first()
    if not model:
        return None
    price = PriceVersion.objects.filter(model_slug=model.slug, active=True, effective_from__lte=timezone.now()).order_by("-effective_from", "-created_at").first()
    if not price:
        return None
    return {
        "input_rub_per_million": str(price.input_rub_per_million),
        "output_rub_per_million": str(price.output_rub_per_million),
        "example_1k_input_rub": str((price.input_rub_per_million / Decimal("1000")).quantize(Decimal("0.0001"))),
        "example_1k_output_rub": str((price.output_rub_per_million / Decimal("1000")).quantize(Decimal("0.0001"))),
    }


def _provider_error_code(response: httpx.Response, prefix="http") -> str:
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            raw = error.get("code") or error.get("type")
            if raw:
                return str(raw)[:80]
    except Exception:
        pass
    return f"{prefix}_{response.status_code}"[:80]


def _check_key(provider: Provider, key: ProviderApiKey):
    started = time.monotonic()
    now = timezone.now()
    if provider.slug == "gigachat":
        health = _gigachat_adapter(provider, key.get_secret()).health_check()
        key.health_state = ProviderApiKey.HealthState.HEALTHY if health.healthy else ProviderApiKey.HealthState.DEGRADED
        key.last_error_code = health.error_code
        key.last_latency_ms = health.latency_ms
        key.last_checked_at = now
        key.save(update_fields=["health_state", "last_error_code", "last_latency_ms", "last_checked_at"])
        return health.healthy
    url = _openrouter_key_url(provider) if provider.slug == "openrouter" else _models_url(provider)
    try:
        response = httpx.get(url, headers=_headers(provider, key.get_secret()), timeout=10, follow_redirects=True)
        response.raise_for_status()
        if provider.slug == "openrouter":
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                raise ValueError("OpenRouter /key returned invalid payload")
        key.health_state = ProviderApiKey.HealthState.HEALTHY
        key.last_error_code = ""
        key.last_latency_ms = int((time.monotonic() - started) * 1000)
    except httpx.TimeoutException:
        key.health_state = ProviderApiKey.HealthState.DEGRADED
        key.last_error_code = "timeout"
        key.last_latency_ms = int((time.monotonic() - started) * 1000)
    except httpx.HTTPStatusError as exc:
        key.health_state = ProviderApiKey.HealthState.DEGRADED
        key.last_error_code = _provider_error_code(exc.response, "http")
        key.last_latency_ms = int((time.monotonic() - started) * 1000)
    except (httpx.HTTPError, ValueError):
        key.health_state = ProviderApiKey.HealthState.DEGRADED
        key.last_error_code = "invalid_response" if provider.slug == "openrouter" else "network"
        key.last_latency_ms = int((time.monotonic() - started) * 1000)
    key.last_checked_at = now
    key.save(update_fields=["health_state", "last_error_code", "last_latency_ms", "last_checked_at"])
    return key.health_state == ProviderApiKey.HealthState.HEALTHY


def _refresh_balance(provider: Provider, key: ProviderApiKey):
    key.balance_supported = False
    key.balance_amount = None
    key.balance_currency = ""
    if provider.adapter_type == Provider.AdapterType.DEEPSEEK_CHAT and provider.slug not in {"openrouter", "gigachat"}:
        try:
            response = httpx.get(f"{provider.api_base_url.rstrip('/')}/user/balance", headers=_headers(provider, key.get_secret()), timeout=10)
            response.raise_for_status()
            balances = (response.json() or {}).get("balance_infos") or []
            if balances:
                preferred = next((x for x in balances if x.get("currency") == "USD"), balances[0])
                key.balance_amount = Decimal(str(preferred.get("total_balance") or "0"))
                key.balance_currency = str(preferred.get("currency") or "")
                key.balance_supported = True
        except Exception:
            pass
    elif provider.slug == "openrouter":
        try:
            response = httpx.get(f"{provider.api_base_url.rstrip('/')}/credits", headers=_headers(provider, key.get_secret()), timeout=10, follow_redirects=True)
            response.raise_for_status()
            data = (response.json() or {}).get("data") or {}
            key.balance_amount = Decimal(str(data.get("total_credits") or "0")) - Decimal(str(data.get("total_usage") or "0"))
            key.balance_currency = "USD"
            key.balance_supported = True
        except Exception:
            pass
    key.balance_checked_at = timezone.now()
    key.save(update_fields=["balance_supported", "balance_amount", "balance_currency", "balance_checked_at"])


class ProviderCredentialView(AdminAPIView):
    def get(self, request, provider_slug):
        provider = get_object_or_404(Provider.objects.prefetch_related("api_keys"), slug=provider_slug)
        return Response(_credential_payload(provider))

    @transaction.atomic
    def patch(self, request, provider_slug):
        provider = get_object_or_404(Provider.objects.select_for_update(), slug=provider_slug)
        if "api_base_url" in request.data:
            base_url = str(request.data.get("api_base_url") or "").strip()
            if base_url:
                try:
                    URLValidator(schemes=["https", "http"])(base_url)
                except Exception:
                    return Response({"detail": "Некорректный URL API"}, status=400)
                provider.api_base_url = base_url
                provider.health_state = Provider.HealthState.UNKNOWN
                provider.save(update_fields=["api_base_url", "health_state"])
        return Response(_credential_payload(provider))


class ProviderKeyCollectionView(AdminAPIView):
    @transaction.atomic
    def post(self, request, provider_slug):
        provider = get_object_or_404(Provider, slug=provider_slug)
        secret = str(request.data.get("api_key") or "").strip()
        if not secret:
            return Response({"detail": "Вставьте API-ключ"}, status=400)
        label = str(request.data.get("label") or f"Ключ {provider.api_keys.count()+1}").strip()[:120]
        if provider.api_keys.filter(label=label).exists():
            label = f"{label} {provider.api_keys.count()+1}"[:120]
        item = ProviderApiKey(provider=provider, label=label, priority=provider.api_keys.count() * 10 + 10)
        item.set_secret(secret)
        item.save()
        healthy = _check_key(provider, item)
        if provider.slug in {"openrouter", "gigachat"} and not healthy:
            error_code = item.last_error_code or "key_validation_failed"
            item.delete()
            provider.health_state = Provider.HealthState.HEALTHY if provider.api_keys.filter(enabled=True, health_state=ProviderApiKey.HealthState.HEALTHY).exists() else Provider.HealthState.DEGRADED
            provider.last_checked_at = timezone.now()
            provider.save(update_fields=["health_state", "last_checked_at"])
            label_name = "GigaChat" if provider.slug == "gigachat" else "OpenRouter"
            return Response({"detail": f"{label_name} не подтвердил ключ авторизации: {error_code}", "code": error_code}, status=400)
        _refresh_balance(provider, item)
        provider.health_state = Provider.HealthState.HEALTHY if provider.api_keys.filter(enabled=True, health_state=ProviderApiKey.HealthState.HEALTHY).exists() else Provider.HealthState.DEGRADED
        provider.last_checked_at = timezone.now()
        provider.last_latency_ms = item.last_latency_ms
        provider.save(update_fields=["health_state", "last_checked_at", "last_latency_ms"])
        audit(request, "provider.key.added", "provider", provider.id, metadata={"provider": provider.slug, "key_id": str(item.id), "healthy": healthy})
        return Response(_key_payload(item), status=201)


class ProviderKeyDetailView(AdminAPIView):
    @transaction.atomic
    def patch(self, request, provider_slug, key_id):
        item = get_object_or_404(ProviderApiKey.objects.select_for_update(), id=key_id, provider__slug=provider_slug)
        if "enabled" in request.data:
            item.enabled = bool(request.data["enabled"])
            item.health_state = ProviderApiKey.HealthState.UNKNOWN if item.enabled else ProviderApiKey.HealthState.DISABLED
        if request.data.get("recheck"):
            item.enabled = True
            item.save(update_fields=["enabled"])
            _check_key(item.provider, item)
            _refresh_balance(item.provider, item)
        else:
            item.save()
        return Response(_key_payload(item))

    @transaction.atomic
    def delete(self, request, provider_slug, key_id):
        item = get_object_or_404(ProviderApiKey.objects.select_for_update(), id=key_id, provider__slug=provider_slug)
        provider_id = item.provider_id
        item.delete()
        audit(request, "provider.key.deleted", "provider", provider_id, metadata={"key_id": str(key_id)})
        return Response(status=204)


class ProviderDiscoveredModelsView(AdminAPIView):
    def get(self, request, provider_slug):
        provider = get_object_or_404(Provider, slug=provider_slug)
        key = provider.api_keys.filter(enabled=True, health_state=ProviderApiKey.HealthState.HEALTHY).order_by("priority", "created_at").first()
        api_key = key.get_secret() if key else provider.get_api_key()
        if not api_key:
            return Response({"detail": "Сначала добавьте рабочий API-ключ"}, status=409)
        try:
            if provider.slug == "gigachat":
                adapter = _gigachat_adapter(provider, api_key)
                response = httpx.get(_models_url(provider), headers=adapter._headers(), timeout=15, follow_redirects=True)
            else:
                response = httpx.get(_models_url(provider), headers=_headers(provider, api_key), timeout=15, follow_redirects=True)
            response.raise_for_status()
            models = _extract_models(provider, response.json())
        except httpx.HTTPStatusError as exc:
            return Response({"detail": f"Провайдер вернул HTTP {exc.response.status_code}"}, status=424)
        except Exception:
            return Response({"detail": "Не удалось получить список моделей"}, status=424)
        configured = set(AIModel.objects.filter(provider=provider).values_list("upstream_model", flat=True))
        enriched = [{**item, "purpose": _purpose(item["id"]), "selected": item["id"] in configured, "price": _price_for(provider, item["id"])} for item in models]
        return Response({"provider": provider.slug, "models": enriched})

    @transaction.atomic
    def post(self, request, provider_slug):
        provider = get_object_or_404(Provider, slug=provider_slug)
        model_ids = request.data.get("model_ids") or []
        if not isinstance(model_ids, list) or not model_ids:
            return Response({"detail": "Выберите хотя бы одну модель"}, status=400)
        created = []
        now = timezone.now()
        for raw in model_ids[:50]:
            upstream = str(raw).strip()[:160]
            if not upstream:
                continue
            model = AIModel.objects.filter(provider=provider, upstream_model=upstream).first()
            if not model:
                base = slugify(f"{provider.slug}-{upstream}")[:45] or f"{provider.slug}-model"
                slug = base
                suffix = 2
                while AIModel.objects.filter(slug=slug).exists():
                    slug = f"{base[:40]}-{suffix}"
                    suffix += 1
                model = AIModel.objects.create(provider=provider, slug=slug, display_name=upstream, upstream_model=upstream, enabled=False, capabilities=["text", "streaming"], routing_tags=["admin-selected"])
            if model.current_version_id is None or model.current_version.exact_api_id != upstream:
                ModelVersion.objects.filter(model=model, stage=ModelVersion.Stage.ACTIVE).update(stage=ModelVersion.Stage.RETIRED, retired_at=now)
                version = ModelVersion.objects.create(model=model, version=f"selected-{now.strftime('%Y%m%d%H%M%S%f')}-{len(created)}", exact_api_id=upstream, capabilities=model.capabilities, routing_tags=model.routing_tags, context_window=model.context_window, max_output_tokens=model.max_output_tokens, stage=ModelVersion.Stage.ACTIVE, activated_at=now, release_notes="Выбрано из списка моделей провайдера")
                model.current_version = version
                model.save(update_fields=["current_version"])
            created.append({"id": str(model.id), "slug": model.slug, "upstream_model": upstream})
        audit(request, "provider.models.selected", "provider", provider.id, metadata={"models": [x["upstream_model"] for x in created]})
        return Response({"models": created})


class ProviderModelConfigView(AdminAPIView):
    @transaction.atomic
    def patch(self, request, provider_slug, model_id):
        model = get_object_or_404(AIModel.objects.select_for_update().select_related("provider", "current_version"), pk=model_id, provider__slug=provider_slug)
        upstream_model = str(request.data.get("upstream_model") or "").strip()
        if not upstream_model:
            return Response({"detail": "Укажите модель"}, status=400)
        now = timezone.now()
        ModelVersion.objects.filter(model=model, stage=ModelVersion.Stage.ACTIVE).update(stage=ModelVersion.Stage.RETIRED, retired_at=now)
        version = ModelVersion.objects.create(model=model, version=f"admin-{now.strftime('%Y%m%d%H%M%S%f')}", exact_api_id=upstream_model, capabilities=model.capabilities, routing_tags=model.routing_tags, context_window=model.context_window, max_output_tokens=model.max_output_tokens, stage=ModelVersion.Stage.ACTIVE, activated_at=now, release_notes="Настроено через панель администратора")
        model.upstream_model = upstream_model
        model.current_version = version
        model.save(update_fields=["upstream_model", "current_version"])
        return Response({"id": str(model.id), "slug": model.slug, "upstream_model": model.upstream_model, "current_version": version.version, "enabled": model.enabled})


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
            if action == "enable":
                blocked = {model.slug: _model_enable_blockers(model) for model in models if _model_enable_blockers(model)}
                if blocked:
                    return Response({"detail": "Модель нельзя включить до завершения настройки", "blockers": blocked}, status=409)
            count = queryset.update(enabled=action == "enable")
        else:
            queryset = Provider.objects.filter(id__in=ids)
            providers = list(queryset)
            if action in {"enable", "disable"}:
                if action == "enable":
                    blocked = {provider.slug: _provider_enable_blockers(provider) for provider in providers if _provider_enable_blockers(provider)}
                    if blocked:
                        return Response({"detail": "Провайдера нельзя включить до успешной настройки", "blockers": blocked}, status=409)
                count = queryset.update(enabled=action == "enable")
            elif action in {"emergency_disable", "emergency_enable"}:
                count = queryset.update(emergency_disabled=action == "emergency_disable")
            else:
                return Response({"detail": "Недопустимое действие с провайдером"}, status=400)
        audit(request, f"{target}.{action}", target, metadata={"ids": ids, "count": count})
        return Response({"updated": count})
