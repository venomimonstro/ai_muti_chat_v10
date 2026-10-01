from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.ai_registry.adapters import ProviderError

from . import runtime_readiness, streaming


def _chain_has_marker(callable_obj, marker, raw_attr):
    current = callable_obj
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, marker, False):
            return True
        current = getattr(current, raw_attr, None)
    return False


def test_chat_runtime_guards_are_installed():
    assert getattr(streaming.prepare, "_ai_workspace_single_flight", False) is True
    assert _chain_has_marker(
        streaming.adapter_for,
        "_ai_workspace_runtime_readiness",
        "_raw_adapter_for",
    )
    assert _chain_has_marker(
        streaming.record_failure,
        "_ai_workspace_runtime_readiness",
        "_raw_record_failure",
    )
    assert _chain_has_marker(
        streaming.record_failure,
        "_ai_workspace_procurement_execution",
        "_raw_record_failure",
    )
    assert getattr(streaming.run, "_ai_workspace_terminal_recovery", False) is True


def _model_stub(pk: str):
    fresh = SimpleNamespace(pk=pk)
    manager = Mock()
    manager.select_related.return_value.filter.return_value.first.return_value = fresh
    model_type = SimpleNamespace(objects=manager)
    return fresh, model_type


def test_late_readiness_rejects_stale_candidate_without_provider_call():
    raw_adapter = Mock(return_value=object())
    raw_failure = Mock()
    module = SimpleNamespace(adapter_for=raw_adapter, record_failure=raw_failure)
    runtime_readiness.install(module)

    model = SimpleNamespace(pk="model-1")
    fresh, model_type = _model_stub(model.pk)

    with patch.object(runtime_readiness, "AIModel", model_type), patch.object(
        runtime_readiness, "execution_model_ready", return_value=False
    ):
        with pytest.raises(ProviderError) as caught:
            module.adapter_for(model)

    assert fresh.pk == model.pk
    assert caught.value.code == runtime_readiness.LOCAL_NOT_READY_CODE
    raw_adapter.assert_not_called()

    module.record_failure(SimpleNamespace(), caught.value)
    raw_failure.assert_not_called()


def test_late_readiness_calls_real_adapter_only_after_fresh_check():
    expected = object()
    raw_adapter = Mock(return_value=expected)
    raw_failure = Mock()
    module = SimpleNamespace(adapter_for=raw_adapter, record_failure=raw_failure)
    runtime_readiness.install(module)

    model = SimpleNamespace(pk="model-2")
    fresh, model_type = _model_stub(model.pk)

    with patch.object(runtime_readiness, "AIModel", model_type), patch.object(
        runtime_readiness, "execution_model_ready", return_value=True
    ):
        result = module.adapter_for(model)

    assert result is expected
    raw_adapter.assert_called_once_with(fresh)
