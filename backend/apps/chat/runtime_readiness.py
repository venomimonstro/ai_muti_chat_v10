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


def execution_model_ready(model: AIModel) -> bool:
    """Last-line readiness immediately before a provider call.

    Commercial funding is intentionally *not* rechecked here. At this point the
    streaming pipeline has already switched RequestCost to this model and the
    procurement post-save hook has atomically reserved provider funds. Requiring
    additional *free* provider balance now would let a request invalidate itself
    when its own reservation consumes the remaining purchased balance.

    The execution guard therefore checks only conditions that can legitimately
    become stale after preflight/reservation: model publication/quarantine,
    provider circuit state and the exact customer-safe runtime credential.
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
    return runtime_credential_ready(provider)


def install(streaming_module) -> None:
    """Revalidate every routed model immediately before customer inference.

    Routing snapshots are durable, while provider/key/model health can change after
    prepare() and after the provider-spend reservation is created. Customer traffic
    must never be used as a health probe for a stale candidate, but a request must
    also never reject itself merely because its own valid procurement reservation
    reduced the account's free balance to zero.

    The local rejection is not a provider failure, so it must not degrade a healthy
    provider/circuit merely because another request changed readiness in the meantime.
    """
    raw_adapter_for = streaming_module.adapter_for
    if getattr(raw_adapter_for, "_ai_workspace_runtime_readiness", False):
        return
    raw_record_failure = streaming_module.record_failure

    def guarded_adapter_for(model, *args, **kwargs):
        fresh = (
            AIModel.objects.select_related("provider", "current_version")
            .filter(pk=model.pk)
            .first()
        )
        if fresh is None or not execution_model_ready(fresh):
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

    # Keep modules that imported these callables by value aligned with the runtime guard.
    for module_name in ("apps.chat.services", "apps.chat.views", "apps.chat.managed_stream"):
        module = sys.modules.get(module_name)
        if module is not None:
            if hasattr(module, "adapter_for"):
                module.adapter_for = guarded_adapter_for
            if hasattr(module, "record_failure"):
                module.record_failure = guarded_record_failure
