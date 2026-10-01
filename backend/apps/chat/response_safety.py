from __future__ import annotations

import sys

from apps.ai_registry.adapters import ProviderError


EMPTY_RESPONSE_CODE = "empty_response"
_raw_adapter_for = None
_raw_record_failure = None


class NonEmptyStreamAdapter:
    """Reject a formally completed stream that produced no meaningful text.

    A blank upstream response must never be billed as a successful customer answer.
    The failure is intentionally local to this attempt: it does not degrade the
    credential/provider because content filters and transient provider bugs can be
    model/request-specific. The chat runtime can then fall back to the next eligible
    model without poisoning provider health.
    """

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def stream(self, *args, **kwargs):
        meaningful = False
        for event in self.inner.stream(*args, **kwargs):
            if getattr(event, "kind", "") == "delta":
                if str(getattr(event, "text_delta", "") or "").strip():
                    meaningful = True
                yield event
                continue
            if getattr(event, "kind", "") == "completed" and not meaningful:
                raise ProviderError(
                    "Provider completed the request without a usable text response",
                    code=EMPTY_RESPONSE_CODE,
                    retryable=False,
                )
            yield event


def install(streaming_module) -> None:
    global _raw_adapter_for, _raw_record_failure

    current_adapter_for = streaming_module.adapter_for
    if getattr(current_adapter_for, "_ai_workspace_nonempty_response", False):
        return

    _raw_adapter_for = current_adapter_for
    _raw_record_failure = streaming_module.record_failure

    def guarded_adapter_for(model, *args, **kwargs):
        return NonEmptyStreamAdapter(_raw_adapter_for(model, *args, **kwargs))

    def guarded_record_failure(provider, error, *args, **kwargs):
        code = str(getattr(error, "code", error) or "").strip().casefold()
        if code == EMPTY_RESPONSE_CODE:
            return None
        return _raw_record_failure(provider, error, *args, **kwargs)

    guarded_adapter_for._ai_workspace_nonempty_response = True
    guarded_adapter_for._raw_adapter_for = current_adapter_for
    guarded_record_failure._ai_workspace_nonempty_response = True
    guarded_record_failure._raw_record_failure = streaming_module.record_failure

    streaming_module.adapter_for = guarded_adapter_for
    streaming_module.record_failure = guarded_record_failure

    # Modules can import these callables by value before AppConfig.ready(). Keep all
    # customer execution entrypoints aligned with the same final adapter contract.
    for module_name in ("apps.chat.services", "apps.chat.views", "apps.chat.managed_stream"):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        if hasattr(module, "adapter_for"):
            module.adapter_for = guarded_adapter_for
        if hasattr(module, "record_failure"):
            module.record_failure = guarded_record_failure
