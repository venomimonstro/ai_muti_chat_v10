from __future__ import annotations

import sys

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.dispatch import runtime_credential_ready
from apps.ai_registry.model_quarantine import model_runtime_available
from apps.ai_registry.models import AIModel, Provider


LOCAL_NOT_READY_CODE = "candidate_not_ready"
SPECIAL_EXTERNAL_PROVIDER_SLUGS = {"gigachat", "openrouter"}


def _is_test_echo_provider(provider: Provider) -> bool:
    return (
        provider.adapter_type == Provider.AdapterType.ECHO
        and provider.slug not in SPECIAL_EXTERNAL_PROVIDER_SLUGS
    )


def _reserved_credential_ready(provider, funding_account_id) -> bool:
    if not funding_account_id:
        return runtime_credential_ready(provider)
    try:
        from apps.procurement.account_routing import account_credential_ready
        from apps.procurement.models import ProviderFundingAccount

        account = (
            ProviderFundingAccount.objects.filter(
                pk=funding_account_id,
                provider=provider,
                active=True,
            )
            .select_related("api_key")
            .first()
        )
        return bool(account and account_credential_ready(account, allow_probe=False))
    except Exception:
        return False


def execution_model_ready(model: AIModel, *, funding_account_id=None) -> bool:
    """Last-line readiness immediately before a provider call.

    When a provider-spend reservation already exists, ``funding_account_id`` is its
    durable credential identity. The balance is not rechecked because those funds are
    already reserved for this Generation; only exact credential health is revalidated.
    """
    if not model.enabled or not str(model.upstream_model or "").strip():
        return False
    provider = model.provider
    if not provider.enabled or provider.emergency_disabled:
        return False
    if provider.health_state == Provider.HealthState.DISABLED:
        return False
    if not model_runtime_available(model):
        return False
    if _is_test_echo_provider(provider):
        return True
    if provider.health_state not in {
        Provider.HealthState.HEALTHY,
        Provider.HealthState.DEGRADED,
    }:
        return False
    return _reserved_credential_ready(provider, funding_account_id)


def install(streaming_module) -> None:
    """Revalidate every routed model immediately before customer inference."""
    raw_adapter_for = streaming_module.adapter_for
    if getattr(raw_adapter_for, "_ai_workspace_runtime_readiness", False) is True:
        return
    raw_record_failure = streaming_module.record_failure

    def guarded_adapter_for(model, *args, **kwargs):
        fresh = (
            AIModel.objects.select_related("provider", "current_version")
            .filter(pk=model.pk)
            .first()
        )
        funding_account_id = kwargs.get("funding_account_id")
        if fresh is None or not execution_model_ready(
            fresh,
            funding_account_id=funding_account_id,
        ):
            raise ProviderError(
                "Routed model is no longer execution-ready",
                retryable=False,
                code=LOCAL_NOT_READY_CODE,
            )
        return raw_adapter_for(fresh, *args, **kwargs)

    def guarded_record_failure(provider, error_code, *args, **kwargs):
        code = str(getattr(error_code, "code", error_code) or "").strip().lower()
        if code == LOCAL_NOT_READY_CODE:
            return None
        return raw_record_failure(provider, error_code, *args, **kwargs)

    guarded_adapter_for._ai_workspace_runtime_readiness = True
    guarded_adapter_for._raw_adapter_for = raw_adapter_for
    guarded_record_failure._ai_workspace_runtime_readiness = True
    guarded_record_failure._raw_record_failure = raw_record_failure
    streaming_module.adapter_for = guarded_adapter_for
    streaming_module.record_failure = guarded_record_failure

    for module_name in ("apps.chat.services", "apps.chat.views", "apps.chat.managed_stream"):
        module = sys.modules.get(module_name)
        if module is not None:
            if hasattr(module, "adapter_for"):
                module.adapter_for = guarded_adapter_for
            if hasattr(module, "record_failure"):
                module.record_failure = guarded_record_failure
