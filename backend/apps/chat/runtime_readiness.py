from __future__ import annotations

import sys

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.models import AIModel
from apps.ai_registry.readiness import model_client_ready


LOCAL_NOT_READY_CODE = "candidate_not_ready"


def install(streaming_module) -> None:
    """Revalidate every routed model immediately before customer inference.

    Routing snapshots are intentionally durable, but provider/key/funding/model health can
    change after prepare() and before a fallback attempt. Customer traffic must never be
    used as a health probe for a stale candidate. This guard refreshes the model/provider
    state just before adapter selection and rejects stale candidates locally.

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
        if fresh is None or not model_client_ready(fresh):
            raise ProviderError(
                "Routed model is no longer customer-ready",
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
