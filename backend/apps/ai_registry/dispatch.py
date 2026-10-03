"""Fail-safe provider adapter dispatch.

Customer traffic and provider health probes deliberately use different credential
selection rules. Customer requests may use only a verified HEALTHY credential. When
procurement is configured, a request can be pinned to the exact funding account that
owns its provider-spend reservation, so API execution and purchasing ledger can never
drift to different credentials.
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


def _funding_credential(
    provider: Provider,
    *,
    allow_probe: bool,
    touch: bool,
    funding_account_id=None,
    require_funding_balance: bool = True,
):
    try:
        from apps.procurement.account_routing import (
            NATIVE_STEP,
            account_credential_ready,
            account_secret,
            select_runtime_funding_account,
        )
        from apps.procurement.models import ProviderFundingAccount

        accounts_exist = ProviderFundingAccount.objects.filter(
            provider=provider, active=True
        ).exists()
        if not accounts_exist:
            return None

        if funding_account_id:
            account = (
                ProviderFundingAccount.objects.filter(
                    pk=funding_account_id,
                    provider=provider,
                    active=True,
                )
                .select_related("api_key")
                .first()
            )
            if account is None:
                return "", None
            if not account_credential_ready(account, allow_probe=allow_probe):
                return "", account.api_key_id
        else:
            account = select_runtime_funding_account(
                provider,
                required_native=NATIVE_STEP if require_funding_balance else 0,
                allow_probe=allow_probe,
                require_balance=require_funding_balance,
            )
            if account is None:
                # When no concrete reservation pins execution to a funding account,
                # local procurement metadata must not shadow a valid HEALTHY API key.
                # Strict balance enforcement belongs to provider-spend reservation,
                # while transport readiness may fall back to the verified key pool.
                return ("", None) if require_funding_balance else None

        value, key_id = account_secret(account)
        if value and touch and key_id:
            ProviderApiKey.objects.filter(pk=key_id).update(last_used_at=timezone.now())
        return value, key_id
    except Exception:
        if funding_account_id:
            return "", None
        return None


def select_runtime_api_key(
    provider: Provider,
    *,
    allow_probe: bool = False,
    touch: bool = True,
    funding_account_id=None,
    require_funding_balance: bool = True,
):
    funded = _funding_credential(
        provider,
        allow_probe=allow_probe,
        touch=touch,
        funding_account_id=funding_account_id,
        require_funding_balance=require_funding_balance,
    )
    if funded is not None:
        return funded

    allowed_states = [ProviderApiKey.HealthState.HEALTHY]
    if allow_probe:
        allowed_states += [
            ProviderApiKey.HealthState.UNKNOWN,
            ProviderApiKey.HealthState.DEGRADED,
        ]

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
    # Transport readiness answers only whether customer traffic has a verified
    # credential. Procurement capacity is checked/reserved separately. Coupling
    # these two concerns made a healthy provider disappear from routing whenever
    # local procurement accounting lagged the real upstream account.
    value, _key_id = select_runtime_api_key(
        provider,
        allow_probe=False,
        touch=False,
        require_funding_balance=False,
    )
    return bool(value)


def _bind_runtime_identity(
    adapter,
    *,
    key_id,
    model,
    allow_probe,
    funding_account_id=None,
):
    try:
        adapter._ai_workspace_key_id = str(key_id) if key_id else ""
        adapter._ai_workspace_model_slug = str(model.slug)
        adapter._ai_workspace_probe_mode = bool(allow_probe)
        adapter._ai_workspace_funding_account_id = (
            str(funding_account_id) if funding_account_id else ""
        )
    except Exception:
        pass
    return adapter


def adapter_for(
    model,
    *,
    allow_probe: bool = False,
    funding_account_id=None,
    require_funding_balance: bool = True,
):
    provider = model.provider
    api_key, key_id = select_runtime_api_key(
        provider,
        allow_probe=allow_probe,
        funding_account_id=funding_account_id,
        require_funding_balance=require_funding_balance,
    )

    if funding_account_id and provider.adapter_type != Provider.AdapterType.ECHO and not api_key:
        raise adapters.ProviderError(
            "Reserved funding credential is no longer execution-ready",
            code="candidate_not_ready",
            retryable=False,
        )

    if provider.slug == "gigachat":
        from .gigachat_adapter import GigaChatAPIAdapter

        adapter = GigaChatAPIAdapter(
            authorization_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("GIGACHAT_API_BASE_URL", "https://api.giga.chat/v1"),
            scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        )
        return _bind_runtime_identity(
            adapter,
            key_id=key_id,
            model=model,
            allow_probe=allow_probe,
            funding_account_id=funding_account_id,
        )
    if provider.slug == "openrouter":
        adapter = adapters.OpenRouterChatAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai/api/v1"),
        )
        return _bind_runtime_identity(
            adapter,
            key_id=key_id,
            model=model,
            allow_probe=allow_probe,
            funding_account_id=funding_account_id,
        )
    if provider.slug == "polza":
        adapter = adapters.PolzaChatAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("POLZA_API_BASE_URL", "https://polza.ai/api/v1"),
        )
        return _bind_runtime_identity(
            adapter,
            key_id=key_id,
            model=model,
            allow_probe=allow_probe,
            funding_account_id=funding_account_id,
        )
    if provider.slug == "hubai":
        adapter = adapters.HubAIChatAdapter(
            api_key=api_key,
            base_url=provider.api_base_url or "https://hubai.loe.gg/v1",
        )
        return _bind_runtime_identity(
            adapter,
            key_id=key_id,
            model=model,
            allow_probe=allow_probe,
            funding_account_id=funding_account_id,
        )

    if provider.adapter_type == Provider.AdapterType.ECHO:
        return _bind_runtime_identity(
            adapters.EchoProviderAdapter(),
            key_id=None,
            model=model,
            allow_probe=allow_probe,
            funding_account_id=funding_account_id,
        )
    if provider.adapter_type == Provider.AdapterType.OPENAI_RESPONSES:
        adapter = adapters.OpenAIResponsesAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
        )
    elif provider.adapter_type == Provider.AdapterType.ANTHROPIC_MESSAGES:
        adapter = adapters.AnthropicMessagesAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("ANTHROPIC_API_BASE_URL", "https://api.anthropic.com/v1"),
        )
    elif provider.adapter_type == Provider.AdapterType.DEEPSEEK_CHAT:
        adapter = adapters.DeepSeekChatAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com"),
        )
    elif provider.adapter_type == Provider.AdapterType.GEMINI_GENERATE_CONTENT:
        adapter = adapters.GeminiGenerateContentAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
        )
    elif provider.adapter_type == Provider.AdapterType.XAI_CHAT:
        adapter = adapters.XAIChatAdapter(
            api_key=api_key,
            base_url=provider.api_base_url
            or os.getenv("XAI_API_BASE_URL", "https://api.x.ai/v1"),
        )
    else:
        raise adapters.ProviderError(
            f"Unsupported adapter: {provider.adapter_type}",
            code="unsupported_adapter",
            retryable=False,
        )
    return _bind_runtime_identity(
        adapter,
        key_id=key_id,
        model=model,
        allow_probe=allow_probe,
        funding_account_id=funding_account_id,
    )
