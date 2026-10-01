import pytest

from apps.ai_registry.adapters import ProviderError, ProviderStreamEvent

from . import retry_adapter, streaming


class _Credential:
    def __init__(self, name):
        self.name = name


class _FailingAdapter:
    def __init__(self, credential):
        self.credential = credential

    def stream(self, **_kwargs):
        raise ProviderError("temporary upstream failure", code="timeout", retryable=True)
        yield  # pragma: no cover


class _WorkingAdapter:
    def __init__(self, credential):
        self.credential = credential

    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="ok")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="retry-refresh-ok",
            input_tokens=3,
            output_tokens=1,
        )


def test_attempt_scoped_adapter_resolves_a_fresh_credential_for_every_retry():
    key_a = _Credential("key-a")
    key_b = _Credential("key-b")
    resolved = []

    def factory():
        if not resolved:
            adapter = _FailingAdapter(key_a)
        else:
            adapter = _WorkingAdapter(key_b)
        resolved.append(adapter.credential.name)
        return adapter

    adapter = retry_adapter.AttemptScopedAdapter(factory)

    with pytest.raises(ProviderError) as captured:
        list(adapter.stream(model="model", messages=[], max_output_tokens=8))
    assert captured.value.code == "timeout"
    assert adapter.credential is key_a

    events = list(adapter.stream(model="model", messages=[], max_output_tokens=8))

    assert resolved == ["key-a", "key-b"]
    assert adapter.credential is key_b
    assert [event.kind for event in events] == ["delta", "completed"]
    assert events[0].text_delta == "ok"


def test_chat_runtime_uses_attempt_scoped_adapter_resolution():
    assert getattr(streaming.adapter_for, "_ai_workspace_attempt_scoped_adapter", False) is True
    proxy = streaming.adapter_for
    assert getattr(proxy, "_raw_adapter_for", None) is not None
