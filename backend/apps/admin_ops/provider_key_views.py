import base64

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.response import Response

from apps.ai_registry.gigachat_adapter import VALID_SCOPES
from apps.ai_registry.models import Provider, ProviderApiKey

from .provider_views import (
    ProviderKeyCollectionView as LegacyProviderKeyCollectionView,
    _check_key,
    _gigachat_scope,
    _key_payload,
    _refresh_balance,
)
from .services import audit


def _wake_provider_recovery():
    try:
        from .tasks import provider_health_watch_task

        provider_health_watch_task.delay()
    except Exception:
        # The periodic minute heartbeat remains the durable retry path.
        pass


def _set_pending_verification(provider: Provider):
    """Never turn a credential-level success into provider-level health proof."""
    if provider.health_state == Provider.HealthState.HEALTHY:
        return False
    provider.health_state = Provider.HealthState.UNKNOWN
    provider.save(update_fields=["health_state"])
    if provider.enabled and provider.models.filter(enabled=True).exists():
        transaction.on_commit(_wake_provider_recovery)
    return True


class SafeProviderKeyCollectionView(LegacyProviderKeyCollectionView):
    """Add a provider credential without bypassing the inference recovery circuit.

    ``_check_key`` proves authentication / metadata access only. A provider that was
    OPEN, DEGRADED or UNKNOWN must still pass ``provider_health_watch_task`` which in
    turn performs a tiny real inference before customer traffic is re-admitted.
    """

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
            if provider.health_state == Provider.HealthState.HEALTHY:
                # Credential semantics changed; require a fresh provider proof.
                provider.health_state = Provider.HealthState.UNKNOWN
            provider.save(update_fields=["auth_config", "health_state"])
            client_id = str(request.data.get("client_id") or "").strip()
            client_secret = str(request.data.get("client_secret") or "").strip()
            if not secret and (client_id or client_secret):
                if not client_id or not client_secret:
                    return Response(
                        {"detail": "Для подключения по Client ID укажите и Client ID, и Client Secret"},
                        status=400,
                    )
                secret = base64.b64encode(
                    f"{client_id}:{client_secret}".encode("utf-8")
                ).decode("ascii")
            if not secret:
                return Response(
                    {"detail": "Укажите Authorization Key либо пару Client ID + Client Secret"},
                    status=400,
                )
        elif not secret:
            return Response({"detail": "Вставьте API-ключ"}, status=400)

        label = str(
            request.data.get("label")
            or (
                "GigaChat OAuth"
                if provider.slug == "gigachat"
                else f"Ключ {provider.api_keys.count() + 1}"
            )
        ).strip()[:120]
        if provider.api_keys.filter(label=label).exists():
            label = f"{label} {provider.api_keys.count() + 1}"[:120]

        item = ProviderApiKey(
            provider=provider,
            label=label,
            priority=provider.api_keys.count() * 10 + 10,
        )
        item.set_secret(secret)
        item.save()
        healthy = _check_key(provider, item)

        if provider.slug in {"openrouter", "gigachat"} and not healthy:
            error_code = item.last_error_code or "key_validation_failed"
            item.delete()
            has_spare = provider.api_keys.filter(
                enabled=True,
                health_state=ProviderApiKey.HealthState.HEALTHY,
            ).exists()
            if not has_spare:
                provider.health_state = Provider.HealthState.DEGRADED
                provider.save(update_fields=["health_state"])
            if provider.slug == "gigachat":
                if error_code == "gigachat_oauth_http_401":
                    detail = (
                        "GigaChat отклонил Authorization Key. Проверьте Authorization Key "
                        "либо Client ID + Client Secret."
                    )
                elif error_code == "gigachat_oauth_http_400":
                    detail = (
                        "GigaChat отклонил OAuth-запрос. Проверьте Scope и тип проекта GigaChat API."
                    )
                elif error_code == "gigachat_oauth_network":
                    detail = (
                        "Не удалось соединиться с OAuth GigaChat. Проверьте сеть и доверенные "
                        "сертификаты на сервере."
                    )
                else:
                    detail = f"GigaChat не подтвердил авторизацию: {error_code}"
            else:
                detail = f"OpenRouter не подтвердил ключ авторизации: {error_code}"
            return Response({"detail": detail, "code": error_code}, status=400)

        _refresh_balance(provider, item)
        verification_pending = _set_pending_verification(provider)
        audit(
            request,
            "provider.key.added",
            "provider",
            provider.id,
            metadata={
                "provider": provider.slug,
                "key_id": str(item.id),
                "healthy": healthy,
                "verification_pending": verification_pending,
            },
        )
        payload = _key_payload(item)
        payload["provider_verification_pending"] = verification_pending
        return Response(payload, status=201)
