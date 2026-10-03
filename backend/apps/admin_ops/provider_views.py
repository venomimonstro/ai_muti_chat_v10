import base64
import os
import time
from decimal import Decimal

import httpx
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.response import Response

from apps.ai_registry.gigachat_adapter import GigaChatAPIAdapter, VALID_SCOPES
from apps.ai_registry.models import AIModel, ModelVersion, Provider, ProviderApiKey
from apps.ai_registry.reliability import provider_available
from apps.billing.models import PriceVersion

from .services import audit
from .views import AdminAPIView


def _provider_enable_blockers(provider: Provider):
    blockers = []
    if not provider.credential_configured() and provider.adapter_type != Provider.AdapterType.ECHO:
        blockers.append("Не настроен API-ключ провайдера")
    # Enabling transport and admitting customer traffic are separate transitions.
    # A disabled provider is deliberately marked DISABLED by the watcher, so requiring
    # HEALTHY here creates an impossible enable -> health recovery deadlock.
    return blockers


def _model_enable_blockers(model: AIModel):
    blockers = []
    if not model.provider.enabled or model.provider.emergency_disabled:
        blockers.append("Провайдер модели выключен")
    blockers.extend(_provider_enable_blockers(model.provider))
    if not provider_available(model.provider):
        blockers.append(
            "Клиентский runtime провайдера не готов: проверьте HEALTHY ключ и закупочный баланс"
        )
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


def _customer_traffic_blockers(provider: Provider):
    """Explain why a transport-healthy provider is still unavailable to customers."""
    if provider_available(provider):
        return []
    blockers = []
    if not provider.enabled:
        blockers.append("Провайдер выключен")
    if provider.emergency_disabled:
        blockers.append("Включён аварийный запрет клиентского трафика")
    if provider.health_state not in {
        Provider.HealthState.HEALTHY,
        Provider.HealthState.DEGRADED,
    }:
        blockers.append(f"Runtime health: {provider.health_state}")
    if provider.adapter_type != Provider.AdapterType.ECHO:
        has_healthy_key = provider.api_keys.filter(
            enabled=True,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        ).exists()
        if provider.api_keys.filter(enabled=True).exists() and not has_healthy_key:
            blockers.append("Нет HEALTHY API-ключа для клиентского трафика")
        active_accounts = provider.funding_accounts.filter(
            active=True,
            funded_native__gt=0,
        )
        if active_accounts.exists():
            funded = active_accounts.filter(
                funded_native__gt=F("spent_native") + F("reserved_native")
            ).exists()
            if not funded:
                blockers.append("Закупленный баланс провайдера исчерпан")
    if not blockers:
        blockers.append(
            "Клиентский runtime не готов: проверьте ключ, funding account, закупочный баланс и pricing"
        )
    return blockers


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
        "available_models_count": len(item.available_models or []),
        "allowed_models_count": len(item.allowed_models or []),
        "allowed_models": list(item.allowed_models or []),
    }


def _gigachat_scope(provider: Provider) -> str:
    config = provider.auth_config or {}
    value = str(config.get("scope") or os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")).strip()
    return value if value in VALID_SCOPES else "GIGACHAT_API_PERS"


def _credential_payload(provider: Provider):
    customer_blockers = _customer_traffic_blockers(provider)
    return {
        "id": str(provider.id),
        "slug": provider.slug,
        "name": provider.name,
        "adapter_type": provider.adapter_type,
        "api_base_url": provider.api_base_url,
        "credential_configured": provider.credential_configured(),
        "credential_source": provider.credential_source(),
        "health_state": provider.health_state,
        "customer_traffic_ready": not customer_blockers,
        "customer_traffic_blockers": customer_blockers,
        "last_checked_at": provider.last_checked_at,
        "last_latency_ms": provider.last_latency_ms,
        "gigachat_scope": _gigachat_scope(provider) if provider.slug == "gigachat" else None,
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


HUBAI_MODELS = (
    ("deepseek-chat-fast", "DeepSeek V3 Fast"),
    ("deepseek-reasoner-fast", "DeepSeek R1 Fast"),
    ("deepseek-chat", "DeepSeek V3"),
    ("deepseek-reasoner", "DeepSeek R1"),
)


def _hubai_catalog(provider: Provider):
    configured = {
        item.upstream_model: item
        for item in AIModel.objects.filter(provider=provider)
    }
    return [
        {
            "id": model_id,
            "display_name": display_name,
            "purpose": _purpose(model_id),
            "selected": model_id in configured,
            "price": _price_for(provider, model_id),
        }
        for model_id, display_name in HUBAI_MODELS
    ]


def _check_hubai_key(provider: Provider, key: ProviderApiKey):
    started = time.monotonic()
    now = timezone.now()
    headers = _headers(provider, key.get_secret())
    error_code = ""
    healthy = False
    try:
        response = httpx.get(
            _models_url(provider),
            headers=headers,
            timeout=10,
            follow_redirects=True,
        )
        if response.status_code in {404, 405}:
            response = httpx.post(
                f"{provider.api_base_url.rstrip('/')}/chat/completions",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat-fast",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "stream": False,
                },
                timeout=10,
                follow_redirects=True,
            )
        response.raise_for_status()
        healthy = True
    except httpx.TimeoutException:
        error_code = "timeout"
    except httpx.HTTPStatusError as exc:
        error_code = _provider_error_code(exc.response, "hubai_http")
    except httpx.HTTPError:
        error_code = "network"
    key.health_state = (
        ProviderApiKey.HealthState.HEALTHY
        if healthy
        else ProviderApiKey.HealthState.DEGRADED
    )
    key.last_error_code = "" if healthy else error_code or "hubai_validation_failed"
    key.last_latency_ms = int((time.monotonic() - started) * 1000)
    key.last_checked_at = now
    key.save(
        update_fields=[
            "health_state",
            "last_error_code",
            "last_latency_ms",
            "last_checked_at",
        ]
    )
    return healthy


def _openrouter_key_url(provider: Provider):
    return f"{provider.api_base_url.rstrip('/')}/key"


def _gigachat_adapter(provider: Provider, secret: str):
    return GigaChatAPIAdapter(
        authorization_key=secret,
        base_url=provider.api_base_url or "https://api.giga.chat/v1",
        scope=_gigachat_scope(provider),
    )


def _polza_chat_model(item: dict, model_id: str) -> bool:
    """Keep Polza discovery scoped to models usable by /chat/completions.

    Prefer explicit endpoint/task metadata when Polza returns it. For older/minimal
    /models payloads, conservatively exclude model families that are clearly for
    image/audio/video/embedding endpoints.
    """
    raw_endpoints = (
        item.get("endpoints")
        or item.get("supported_endpoints")
        or item.get("api_endpoints")
        or []
    )
    if isinstance(raw_endpoints, str):
        raw_endpoints = [raw_endpoints]
    endpoints = {str(value).strip().lower() for value in raw_endpoints if value}
    if endpoints:
        return any("chat/completions" in value for value in endpoints)

    task = str(
        item.get("task")
        or item.get("type")
        or item.get("category")
        or ""
    ).strip().lower()
    if task:
        if any(token in task for token in ("chat", "text", "language", "code", "reason")):
            return True
        if any(token in task for token in ("image", "audio", "speech", "video", "embedding", "music")):
            return False

    value = model_id.casefold()
    excluded = (
        "embedding",
        "whisper",
        "transcrib",
        "tts",
        "speech",
        "text-to-speech",
        "image-generation",
        "image_gen",
        "gpt-image",
        "dall-e",
        "sora",
        "veo",
        "lyria",
        "video",
        "music",
    )
    return not any(token in value for token in excluded)


def _positive_int(*values, default=0):
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return default


def _model_capabilities(item: dict, model_id: str) -> list[str]:
    capabilities = {"text", "streaming"}
    raw = (
        item.get("capabilities")
        or item.get("modalities")
        or item.get("input_modalities")
        or []
    )
    if isinstance(raw, str):
        raw = [raw]
    normalized = {str(value).strip().lower() for value in raw if value}
    if normalized.intersection({"image", "images", "vision", "multimodal"}):
        capabilities.add("vision")
    if normalized.intersection({"tool", "tools", "function_calling", "functions"}):
        capabilities.add("tools")
    # Some catalogs expose modalities nested under architecture.
    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    nested = architecture.get("input_modalities") or architecture.get("modalities") or []
    if isinstance(nested, str):
        nested = [nested]
    nested_normalized = {str(value).strip().lower() for value in nested if value}
    if nested_normalized.intersection({"image", "images", "vision", "multimodal"}):
        capabilities.add("vision")
    return sorted(capabilities)


def _model_metadata(item: dict, model_id: str) -> dict:
    top_provider = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
    context_window = _positive_int(
        item.get("context_length"),
        item.get("context_window"),
        item.get("max_context_length"),
        top_provider.get("context_length"),
        default=8192,
    )
    max_output = _positive_int(
        item.get("max_output_tokens"),
        item.get("max_completion_tokens"),
        item.get("max_tokens"),
        top_provider.get("max_completion_tokens"),
        default=min(4096, max(512, context_window // 4)),
    )
    return {
        "context_window": max(512, context_window),
        "max_output_tokens": max(64, min(max_output, context_window)),
        "capabilities": _model_capabilities(item, model_id),
    }


def _extract_models(provider: Provider, payload):
    raw = payload.get("models", []) if provider.adapter_type == Provider.AdapterType.GEMINI_GENERATE_CONTENT else payload.get("data", [])
    result = []
    for item in raw:
        model_id = str(item.get("name") or item.get("id") or "").strip()
        if model_id.startswith("models/"):
            model_id = model_id[7:]
        if not model_id:
            continue
        if provider.slug == "polza" and not _polza_chat_model(item, model_id):
            continue
        result.append({
            "id": model_id,
            "display_name": item.get("displayName") or item.get("display_name") or model_id,
            **_model_metadata(item, model_id),
        })
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


def _extract_polza_key_model_scope(payload):
    if not isinstance(payload, dict):
        return []
    candidates = []
    direct_keys = (
        "models", "model_ids", "allowed_models", "allowed_model_ids",
        "enabled_models", "enabled_model_ids",
    )
    for name in direct_keys:
        value = payload.get(name)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    model_id = str(item.get("id") or item.get("model") or item.get("model_id") or "").strip()
                else:
                    model_id = str(item or "").strip()
                if model_id:
                    candidates.append(model_id)
    for container_name in ("restrictions", "permissions", "settings", "limits", "access"):
        nested = payload.get(container_name)
        if isinstance(nested, dict):
            candidates.extend(_extract_polza_key_model_scope(nested))
    return list(dict.fromkeys(candidates))


def _sync_polza_key_scope(provider: Provider, key: ProviderApiKey):
    secret = key.get_secret()
    base_url = (provider.api_base_url or "https://polza.ai/api/v1").rstrip("/")
    auth_headers = _headers(provider, secret)

    key_response = httpx.get(
        f"{base_url}/key",
        headers=auth_headers,
        timeout=10,
        follow_redirects=True,
    )
    key_response.raise_for_status()
    key_payload = key_response.json() if key_response.content else {}

    auth_response = httpx.get(
        f"{base_url}/models",
        headers=auth_headers,
        timeout=15,
        follow_redirects=True,
    )
    auth_response.raise_for_status()
    auth_payload = auth_response.json()
    auth_models = sorted({
        str(item.get("id") or item.get("name") or "").strip()
        for item in ((auth_payload or {}).get("data") or [])
        if isinstance(item, dict) and str(item.get("id") or item.get("name") or "").strip()
    })

    restricted = _extract_polza_key_model_scope(key_payload)
    source = ""
    if restricted:
        allowed = [model_id for model_id in restricted if not auth_models or model_id in auth_models]
        source = "polza_key"
    else:
        public_models = []
        try:
            public_response = httpx.get(
                f"{base_url}/models",
                headers={"accept-language": "ru"},
                timeout=15,
                follow_redirects=True,
            )
            public_response.raise_for_status()
            public_payload = public_response.json()
            public_models = sorted({
                str(item.get("id") or item.get("name") or "").strip()
                for item in ((public_payload or {}).get("data") or [])
                if isinstance(item, dict) and str(item.get("id") or item.get("name") or "").strip()
            })
        except Exception:
            public_models = []
        if public_models and set(auth_models) < set(public_models):
            allowed = auth_models
            source = "authorized_models_subset"
        elif key.allowed_models:
            allowed = [model_id for model_id in key.allowed_models if not auth_models or model_id in auth_models]
            source = key.model_scope_source or "legacy_preserved"
        else:
            allowed = []
            source = "polza_scope_not_exposed"

    key.available_models = auth_models
    key.allowed_models = sorted(dict.fromkeys(allowed))
    key.model_scope_source = source
    return key_payload


def _ensure_polza_registry_models(provider: Provider, model_ids):
    if not model_ids:
        return
    catalog = {}
    try:
        response = httpx.get(
            f"{provider.api_base_url.rstrip('/')}/models",
            headers={"accept-language": "ru"},
            timeout=15,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
        catalog = {
            str(item.get("id") or item.get("name") or "").strip(): item
            for item in ((payload or {}).get("data") or [])
            if isinstance(item, dict) and str(item.get("id") or item.get("name") or "").strip()
        }
    except Exception:
        catalog = {}

    for upstream in model_ids:
        item = catalog.get(upstream) or {}
        model = AIModel.objects.filter(provider=provider, upstream_model=upstream).first()
        if model is None:
            base_slug = slugify(f"polza-{upstream}")[:100] or "polza-model"
            slug = base_slug
            suffix = 2
            while AIModel.objects.filter(slug=slug).exists():
                slug = f"{base_slug[:94]}-{suffix}"
                suffix += 1
            meta = _model_metadata(item, upstream)
            model = AIModel.objects.create(
                provider=provider,
                slug=slug,
                display_name=str(item.get("name") or item.get("display_name") or upstream)[:120],
                upstream_model=upstream,
                enabled=False,
                capabilities=meta.get("capabilities") or ["text", "streaming"],
                routing_tags=["polza", "key-synced"],
                context_window=int(meta.get("context_window") or 8192),
                max_output_tokens=int(meta.get("max_output_tokens") or 2048),
            )
        if model.current_version_id is None:
            version = ModelVersion.objects.create(
                model=model,
                version="polza-key-sync",
                exact_api_id=upstream,
                capabilities=model.capabilities,
                routing_tags=model.routing_tags,
                context_window=model.context_window,
                max_output_tokens=model.max_output_tokens,
                stage=ModelVersion.Stage.ACTIVE,
                activated_at=timezone.now(),
            )
            AIModel.objects.filter(pk=model.pk).update(current_version=version)
            model.current_version = version


def _check_key(provider: Provider, key: ProviderApiKey):
    started = time.monotonic()
    now = timezone.now()
    if provider.slug == "hubai":
        return _check_hubai_key(provider, key)
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
        if provider.slug == "polza":
            _sync_polza_key_scope(provider, key)
            _ensure_polza_registry_models(provider, key.allowed_models)
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
    update_fields = ["health_state", "last_error_code", "last_latency_ms", "last_checked_at"]
    if provider.slug == "polza":
        update_fields.extend(["available_models", "allowed_models", "model_scope_source"])
    key.save(update_fields=update_fields)
    return key.health_state == ProviderApiKey.HealthState.HEALTHY


def _refresh_balance(provider: Provider, key: ProviderApiKey):
    key.balance_supported = False
    key.balance_amount = None
    key.balance_currency = ""
    if provider.adapter_type == Provider.AdapterType.DEEPSEEK_CHAT and provider.slug not in {"openrouter", "gigachat", "hubai"}:
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
        update_fields = []
        if "api_base_url" in request.data:
            base_url = str(request.data.get("api_base_url") or "").strip()
            if base_url:
                try:
                    URLValidator(schemes=["https", "http"])(base_url)
                except Exception:
                    return Response({"detail": "Некорректный URL API"}, status=400)
                provider.api_base_url = base_url
                update_fields.append("api_base_url")
        if provider.slug == "gigachat" and "gigachat_scope" in request.data:
            scope = str(request.data.get("gigachat_scope") or "").strip()
            if scope not in VALID_SCOPES:
                return Response({"detail": "Некорректный Scope GigaChat"}, status=400)
            config = dict(provider.auth_config or {})
            config["scope"] = scope
            provider.auth_config = config
            update_fields.append("auth_config")
        if update_fields:
            provider.health_state = Provider.HealthState.UNKNOWN
            update_fields.append("health_state")
            provider.save(update_fields=list(dict.fromkeys(update_fields)))
        return Response(_credential_payload(provider))


class ProviderKeyCollectionView(AdminAPIView):
    @transaction.atomic
    def post(self, request, provider_slug):
        provider = get_object_or_404(Provider, slug=provider_slug)
        secret = str(request.data.get("api_key") or "").strip()
        if provider.slug == "gigachat":
            scope = str(request.data.get("scope") or _gigachat_scope(provider)).strip()
            if scope not in VALID_SCOPES:
                return Response({"detail": "Выберите корректный Scope GigaChat"}, status=400)
            config = dict(provider.auth_config or {})
            config["scope"] = scope
            provider.auth_config = config
            provider.health_state = Provider.HealthState.UNKNOWN
            provider.save(update_fields=["auth_config", "health_state"])
            client_id = str(request.data.get("client_id") or "").strip()
            client_secret = str(request.data.get("client_secret") or "").strip()
            if not secret and (client_id or client_secret):
                if not client_id or not client_secret:
                    return Response({"detail": "Для подключения по Client ID укажите и Client ID, и Client Secret"}, status=400)
                secret = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
            if not secret:
                return Response({"detail": "Укажите Authorization Key либо пару Client ID + Client Secret"}, status=400)
        elif not secret:
            return Response({"detail": "Вставьте API-ключ"}, status=400)
        label = str(request.data.get("label") or ("GigaChat OAuth" if provider.slug == "gigachat" else f"Ключ {provider.api_keys.count()+1}")).strip()[:120]
        if provider.api_keys.filter(label=label).exists():
            label = f"{label} {provider.api_keys.count()+1}"[:120]
        item = ProviderApiKey(provider=provider, label=label, priority=provider.api_keys.count() * 10 + 10)
        item.set_secret(secret)
        item.save()
        healthy = _check_key(provider, item)
        if provider.slug in {"openrouter", "gigachat", "hubai", "polza"} and not healthy:
            error_code = item.last_error_code or "key_validation_failed"
            item.delete()
            provider.health_state = Provider.HealthState.HEALTHY if provider.api_keys.filter(enabled=True, health_state=ProviderApiKey.HealthState.HEALTHY).exists() else Provider.HealthState.DEGRADED
            provider.last_checked_at = timezone.now()
            provider.save(update_fields=["health_state", "last_checked_at"])
            if provider.slug == "gigachat":
                if error_code == "gigachat_oauth_http_401":
                    detail = "GigaChat отклонил Authorization Key. Проверьте Authorization Key либо Client ID + Client Secret."
                elif error_code == "gigachat_oauth_http_400":
                    detail = "GigaChat отклонил OAuth-запрос. Проверьте Scope и тип проекта GigaChat API."
                elif error_code == "gigachat_oauth_network":
                    detail = "Не удалось соединиться с OAuth GigaChat. Проверьте сеть и доверенные сертификаты на сервере."
                else:
                    detail = f"GigaChat не подтвердил авторизацию: {error_code}"
            elif provider.slug == "hubai":
                detail = f"HubAI не подтвердил API-ключ: {error_code}"
            elif provider.slug == "polza":
                detail = f"Polza.ai не подтвердила API-ключ: {error_code}"
            else:
                detail = f"OpenRouter не подтвердил ключ авторизации: {error_code}"
            return Response({"detail": detail, "code": error_code}, status=400)
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
        if "allowed_models" in request.data:
            if item.provider.slug != "polza":
                return Response({"detail": "Allowlist моделей поддерживается только для router-провайдеров"}, status=400)
            raw_allowed = request.data.get("allowed_models")
            if not isinstance(raw_allowed, list):
                return Response({"detail": "allowed_models должен быть списком"}, status=400)
            available = {str(value) for value in (item.available_models or []) if value}
            normalized = []
            for value in raw_allowed[:200]:
                model_id = str(value or "").strip()[:160]
                if not model_id:
                    continue
                if available and model_id not in available:
                    return Response({"detail": f"Модель {model_id} недоступна этому Polza-ключу"}, status=400)
                if model_id not in normalized:
                    normalized.append(model_id)
            item.allowed_models = normalized
        if request.data.get("recheck"):
            item.enabled = True
            item.save(update_fields=["enabled"])
            _check_key(item.provider, item)
            _refresh_balance(item.provider, item)
            provider = item.provider
            healthy_key = provider.api_keys.filter(
                enabled=True,
                health_state=ProviderApiKey.HealthState.HEALTHY,
            ).exists()
            # /models proves only metadata/auth access. Never re-admit a DEGRADED,
            # OPEN or UNKNOWN provider to customer traffic without the normal tiny
            # real-inference recovery probe.
            if not healthy_key:
                provider.health_state = Provider.HealthState.DEGRADED
            elif provider.health_state != Provider.HealthState.HEALTHY:
                provider.health_state = Provider.HealthState.UNKNOWN
            provider.last_checked_at = timezone.now()
            provider.last_latency_ms = item.last_latency_ms
            provider.save(
                update_fields=[
                    "health_state",
                    "last_checked_at",
                    "last_latency_ms",
                ]
            )
            if (
                healthy_key
                and provider.enabled
                and provider.models.filter(enabled=True).exists()
                and provider.health_state != Provider.HealthState.HEALTHY
            ):
                try:
                    from .tasks import provider_health_watch_task
                    transaction.on_commit(provider_health_watch_task.delay)
                except Exception:
                    pass
        else:
            update_fields = []
            if "enabled" in request.data:
                update_fields.extend(["enabled", "health_state"])
            if "allowed_models" in request.data:
                update_fields.append("allowed_models")
            item.save(update_fields=list(dict.fromkeys(update_fields)) or None)
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
        requested_key_id = str(request.query_params.get("key_id") or "").strip()
        healthy_keys = list(
            provider.api_keys.filter(
                enabled=True,
                health_state=ProviderApiKey.HealthState.HEALTHY,
            ).order_by("priority", "created_at")[:20]
        )
        if requested_key_id:
            healthy_keys = [
                item for item in healthy_keys if str(item.id) == requested_key_id
            ]
        key = healthy_keys[0] if healthy_keys else None
        api_key = key.get_secret() if key else provider.get_api_key()
        if not api_key:
            return Response({"detail": "Сначала добавьте рабочий API-ключ"}, status=409)
        if provider.slug == "hubai":
            return Response({"provider": provider.slug, "models": _hubai_catalog(provider)})
        try:
            if provider.slug == "polza" and healthy_keys:
                # Different Polza credentials may expose different model subsets.
                # Discover through every healthy key and merge by exact upstream id.
                merged = {}
                failures = []
                for item_key in healthy_keys:
                    try:
                        response = httpx.get(
                            _models_url(provider),
                            headers=_headers(provider, item_key.get_secret()),
                            timeout=15,
                            follow_redirects=True,
                        )
                        response.raise_for_status()
                        rows = _extract_models(provider, response.json())
                        for row in rows:
                            existing = merged.get(row["id"])
                            if existing is None:
                                merged[row["id"]] = {
                                    **row,
                                    "available_via_keys": 1,
                                }
                            else:
                                existing["available_via_keys"] = int(
                                    existing.get("available_via_keys") or 1
                                ) + 1
                    except Exception as exc:
                        failures.append(
                            {
                                "key_id": str(item_key.id),
                                "code": getattr(exc, "code", type(exc).__name__),
                            }
                        )
                models = list(merged.values())
                if not models:
                    return Response(
                        {
                            "detail": "Не удалось получить каталог моделей Polza ни по одному рабочему ключу",
                            "key_failures": failures,
                        },
                        status=424,
                    )
            else:
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
        allowed_by_key = {}
        if provider.slug == "polza":
            for item_key in healthy_keys:
                for model_id in (item_key.allowed_models or []):
                    allowed_by_key.setdefault(str(model_id), []).append(str(item_key.id))
        enriched = [
            {
                **item,
                "purpose": _purpose(item["id"]),
                "selected": item["id"] in configured,
                "allowed_key_ids": allowed_by_key.get(item["id"], []),
                "allowed_for_requested_key": (
                    bool(requested_key_id and requested_key_id in allowed_by_key.get(item["id"], []))
                    if provider.slug == "polza"
                    else None
                ),
                "price": _price_for(provider, item["id"]),
            }
            for item in models
        ]
        return Response({"provider": provider.slug, "key_id": requested_key_id or None, "models": enriched})

    @transaction.atomic
    def post(self, request, provider_slug):
        provider = get_object_or_404(Provider, slug=provider_slug)
        model_ids = request.data.get("model_ids") or []
        if not isinstance(model_ids, list):
            return Response({"detail": "model_ids должен быть списком"}, status=400)
        requested_key_id = str(request.data.get("key_id") or "").strip()
        target_key = None
        if provider.slug == "polza":
            if not requested_key_id:
                return Response({"detail": "Для Polza выберите конкретный API-ключ"}, status=400)
            target_key = get_object_or_404(
                ProviderApiKey.objects.select_for_update(),
                pk=requested_key_id,
                provider=provider,
            )
            available = {str(value) for value in (target_key.available_models or []) if value}
            invalid = [str(value) for value in model_ids if available and str(value) not in available]
            if invalid:
                return Response({"detail": f"Модели недоступны этому ключу: {', '.join(invalid[:10])}"}, status=400)
            target_key.allowed_models = list(dict.fromkeys(str(value).strip()[:160] for value in model_ids if str(value).strip()))
            target_key.save(update_fields=["allowed_models"])
        elif not model_ids:
            return Response({"detail": "Выберите хотя бы одну модель"}, status=400)

        catalog_meta = {}
        if provider.slug == "polza":
            healthy_key = target_key or (
                provider.api_keys.filter(
                    enabled=True,
                    health_state=ProviderApiKey.HealthState.HEALTHY,
                )
                .order_by("priority", "created_at")
                .first()
            )
            if healthy_key is not None:
                try:
                    response = httpx.get(
                        _models_url(provider),
                        headers=_headers(provider, healthy_key.get_secret()),
                        timeout=15,
                        follow_redirects=True,
                    )
                    response.raise_for_status()
                    catalog_meta = {
                        item["id"]: item
                        for item in _extract_models(provider, response.json())
                    }
                except Exception:
                    catalog_meta = {}

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
                meta = catalog_meta.get(upstream) or {}
                model = AIModel.objects.create(
                    provider=provider,
                    slug=slug,
                    display_name=str(meta.get("display_name") or upstream)[:120],
                    upstream_model=upstream,
                    enabled=False,
                    capabilities=meta.get("capabilities") or ["text", "streaming"],
                    routing_tags=["admin-selected", provider.slug],
                    context_window=int(meta.get("context_window") or 8192),
                    max_output_tokens=int(meta.get("max_output_tokens") or 2048),
                )
            if provider.slug == "polza":
                meta = catalog_meta.get(upstream) or {}
                update_fields = []
                desired_name = str(meta.get("display_name") or model.display_name or upstream)[:120]
                desired_capabilities = meta.get("capabilities") or model.capabilities or ["text", "streaming"]
                desired_context = int(meta.get("context_window") or model.context_window or 8192)
                desired_output = int(meta.get("max_output_tokens") or model.max_output_tokens or 2048)
                if model.display_name != desired_name:
                    model.display_name = desired_name
                    update_fields.append("display_name")
                if model.capabilities != desired_capabilities:
                    model.capabilities = desired_capabilities
                    update_fields.append("capabilities")
                if model.context_window != desired_context:
                    model.context_window = desired_context
                    update_fields.append("context_window")
                if model.max_output_tokens != desired_output:
                    model.max_output_tokens = desired_output
                    update_fields.append("max_output_tokens")
                tags = list(dict.fromkeys([*(model.routing_tags or []), "admin-selected", "polza"]))
                if model.routing_tags != tags:
                    model.routing_tags = tags
                    update_fields.append("routing_tags")
                if update_fields:
                    model.save(update_fields=update_fields)

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
                    # Re-admission remains fail-closed: background/admin health probe
                    # must prove the provider after it is enabled.
                    count = queryset.update(
                        enabled=True,
                        health_state=Provider.HealthState.UNKNOWN,
                        circuit_opened_until=None,
                    )
                else:
                    count = queryset.update(
                        enabled=False,
                        health_state=Provider.HealthState.DISABLED,
                    )
            elif action in {"emergency_disable", "emergency_enable"}:
                count = queryset.update(emergency_disabled=action == "emergency_disable")
            else:
                return Response({"detail": "Недопустимое действие с провайдером"}, status=400)
        audit(request, f"{target}.{action}", target, metadata={"ids": ids, "count": count})
        return Response({"updated": count})
