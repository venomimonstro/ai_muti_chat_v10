"""Fail-safe provider adapter dispatch.

Customer traffic and provider health probes deliberately use different credential
selection rules. Customer requests may use only a verified HEALTHY credential and,
when procurement is configured, the credential linked to the active default funding
account. Health probes may additionally test UNKNOWN/DEGRADED credentials so recovery
never depends on a real customer request.
"""

import os

from django.db import models
from django.utils import timezone

from . import adapters
from .models import Provider, ProviderApiKey


def _secret(key: ProviderApiKey | None, *, touch: bool):
    if key is None:
        return "", None
    value = key.get_secret()
    if not value:
        return "", key.pk
    if touch:
        ProviderApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
    return value, key.pk


def _default_funding_account(provider: Provider):
    try:
        return (
            provider.funding_accounts.filter(active=True, is_default=True)
            .select_related("api_key")
            .first()
        )
    except Exception:
        return None


def select_runtime_api_key(
    provider: Provider,
    *,
    allow_probe: bool = False,
    touch: bool = True,
):
    """Return the credential permitted for this execution path.

    In commercial mode a default funding account is authoritative. We never send a
    request with another key while charging procurement against the default account.
    This intentionally prefers cross-model/provider fallback over financially
    ambiguous same-provider key rotation.
    """
    allowed_states = [ProviderApiKey.HealthState.HEALTHY]
    if allow_probe:
        allowed_states += [
            ProviderApiKey.HealthState.UNKNOWN,
            ProviderApiKey.HealthState.DEGRADED,
        ]

    funding = _default_funding_account(provider)
    if funding is not None:
        if funding.api_key_id:
            key = funding.api_key
            if not key.enabled or key.health_state not in allowed_states:
                return "", key.pk
            return _secret(key, touch=touch)
        if funding.credential_env:
            return os.getenv(funding.credential_env, "").strip(), None
        return "", None

    try:
        pool_exists = provider.api_keys.filter(enabled=True).exclude(
            health_state=ProviderApiKey.HealthState.DISABLED
        ).exists()
        if pool_exists:
            for health_state in allowed_states:
                keys = (
                    provider.api_keys.filter(enabled=True, health_state=health_state)
                    .order_by(
                        models.F("last_used_at").asc(nulls_first=True),
                        "priority",
                        "created_at",
                    )[:10]
                )
                for key in keys:
                    value, key_id = _secret(key, touch=touch)
                    if value:
                        return value, key_id
            return "", None
    except Exception:
        return "", None

    legacy = provider._legacy_api_key() or (
        os.getenv(provider.credential_env, "").strip() if provider.credential_env else ""
    )
    return legacy, None


def runtime_credential_ready(provider: Provider) -> bool:
    value, _key_id = select_runtime_api_key(provider, allow_probe=False, touch=False)
    return bool(value)


def _bind_key_identity(adapter, key_id):
    # Internal correlation only; the secret itself is never logged or persisted.
    try:
        adapter._ai_workspace_key_id = str(key_id) if key_id else ""
    except Exception:
        pass
    return adapter


def adapter_for(model, *, allow_probe: bool = False):
    provider = model.provider
    api_key, key_id = select_runtime_api_key(provider, allow_probe=allow_probe)

    # Slug-specific production adapters are authoritative and deliberately run
    # before adapter_type. This prevents legacy GigaChat/OpenRouter rows from
    # silently returning Echo responses.
    if provider.slug == "gigachat":
        from .gigachat_adapter import GigaChatAPIAdapter

        return _bind_key_identity(
            GigaChatAPIAdapter(
                authorization_key=api_key,
                base_url=provider.api_base_url
                or os.getenv("GIGACHAT_API_BASE_URL", "https://api.giga.chat/v1"),
                scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
            ),
            key_id,
        )
    if provider.slug == "openrouter":
        return _bind_key_identity(
            adapters.OpenRouterChatAdapter(
                api_key=api_key,
                base_url=provider.api_base_url
                or os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai/api/v1"),
            ),
            key_id,
        )

    if provider.adapter_type == Provider.AdapterType.ECHO:
        return adapters.EchoProviderAdapter()
    if provider.adapter_type == Provider.AdapterType.OPENAI_RESPONSES:
        return _bind_key_identity(
            adapters.OpenAIResponsesAdapter(
                api_key=api_key,
                base_url=provider.api_base_url
                or os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
            ),
            key_id,
        )
    if provider.adapter_type == Provider.AdapterType.ANTHROPIC_MESSAGES:
        return _bind_key_identity(
            adapters.AnthropicMessagesAdapter(
                api_key=api_key,
                base_url=provider.api_base_url
                or os.getenv("ANTHROPIC_API_BASE_URL", "https://api.anthropic.com/v1"),
            ),
            key_id,
        )
    if provider.adapter_type == Provider.AdapterType.DEEPSEEK_CHAT:
        return _bind_key_identity(
            adapters.DeepSeekChatAdapter(
                api_key=api_key,
                base_url=provider.api_base_url
                or os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com"),
            ),
            key_id,
        )
    if provider.adapter_type == Provider.AdapterType.GEMINI_GENERATE_CONTENT:
        return _bind_key_identity(
            adapters.GeminiGenerateContentAdapter(
                api_key=api_key,
                base_url=provider.api_base_url
                or os.getenv("GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
            ),
            key_id,
        )
    if provider.adapter_type == Provider.AdapterType.XAI_CHAT:
        return _bind_key_identity(
            adapters.XAIChatAdapter(
                api_key=api_key,
                base_url=provider.api_base_url
                or os.getenv("XAI_API_BASE_URL", "https://api.x.ai/v1"),
            ),
            key_id,
        )
    raise adapters.ProviderError(
        f"Unsupported adapter: {provider.adapter_type}",
        code="unsupported_adapter",
        retryable=False,
    )
