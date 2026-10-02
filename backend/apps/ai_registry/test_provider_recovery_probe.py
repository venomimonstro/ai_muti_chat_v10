from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from . import dispatch, model_quarantine, reliability
from .adapters import AdapterHealth, ProviderError, ProviderResult
from .models import AIModel, Provider, ProviderHealthSnapshot


class FakeAdapter:
    def __init__(self, *, health=None, result=None, error=None):
        self.health = health or AdapterHealth(True, 4)
        self.result = result or ProviderResult(
            text="OK",
            input_tokens=2,
            output_tokens=1,
            provider_request_id="probe-1",
        )
        self.error = error
        self.health_calls = 0
        self.generate_calls = 0
        self._ai_workspace_key_id = ""

    def health_check(self):
        self.health_calls += 1
        return self.health

    def generate(self, **_kwargs):
        self.generate_calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _provider(slug, state):
    return Provider.objects.create(
        slug=slug,
        name=slug,
        enabled=True,
        health_state=state,
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
    )


def _model(provider, slug):
    return AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model=slug,
        enabled=True,
    )


@pytest.mark.django_db
def test_unhealthy_provider_requires_real_inference_before_recovery(monkeypatch):
    provider = _provider("recovery-credit", Provider.HealthState.DEGRADED)
    model = _model(provider, "recovery-credit-model")
    adapter = FakeAdapter(
        error=ProviderError(
            "no credits",
            code="credit_balance_exhausted",
            retryable=False,
        )
    )
    success = Mock()
    failure = Mock()
    monkeypatch.setattr(dispatch, "adapter_for", lambda selected, **_kwargs: adapter)
    monkeypatch.setattr(reliability, "record_success", success)
    monkeypatch.setattr(reliability, "record_failure", failure)

    health = reliability.check_provider(provider)

    assert model.enabled is True
    assert adapter.health_calls == 1
    assert adapter.generate_calls == 1
    success.assert_not_called()
    failure.assert_called_once()
    assert health.healthy is False
    assert health.error_code == "credit_balance_exhausted"
    snapshot = ProviderHealthSnapshot.objects.filter(provider=provider).latest("checked_at")
    assert snapshot.healthy is False
    assert snapshot.error_code == "credit_balance_exhausted"


@pytest.mark.django_db
def test_healthy_provider_uses_cheap_health_check_without_paid_probe(monkeypatch):
    provider = _provider("healthy-watch", Provider.HealthState.HEALTHY)
    _model(provider, "healthy-watch-model")
    adapter = FakeAdapter()
    success = Mock()
    failure = Mock()
    monkeypatch.setattr(dispatch, "adapter_for", lambda selected, **_kwargs: adapter)
    monkeypatch.setattr(reliability, "record_success", success)
    monkeypatch.setattr(reliability, "record_failure", failure)

    health = reliability.check_provider(provider)

    assert health.healthy is True
    assert adapter.health_calls == 1
    assert adapter.generate_calls == 0
    success.assert_called_once()
    failure.assert_not_called()


@pytest.mark.django_db
def test_model_scoped_recovery_failure_tries_sibling_without_provider_outage(monkeypatch):
    provider = _provider("sibling-recovery", Provider.HealthState.OPEN)
    first = _model(provider, "a-bad-model")
    second = _model(provider, "b-good-model")
    bad_adapter = FakeAdapter(
        error=ProviderError("missing", code="model_not_found", retryable=False)
    )
    good_adapter = FakeAdapter()
    success = Mock()
    failure = Mock()
    quarantined = Mock()

    def adapter_for(model, **_kwargs):
        return bad_adapter if model.pk == first.pk else good_adapter

    monkeypatch.setattr(dispatch, "adapter_for", adapter_for)
    monkeypatch.setattr(reliability, "record_success", success)
    monkeypatch.setattr(reliability, "record_failure", failure)
    monkeypatch.setattr(model_quarantine, "quarantine_model", quarantined)

    health = reliability.check_provider(provider)

    assert health.healthy is True
    assert bad_adapter.health_calls == 1
    assert bad_adapter.generate_calls == 1
    assert good_adapter.generate_calls == 1
    quarantined.assert_called_once()
    failure.assert_not_called()
    success.assert_called_once()
    assert success.call_args.args[0].pk == provider.pk
    assert success.call_args.kwargs["adapter"] is good_adapter


@pytest.mark.django_db
def test_recovery_batch_never_releases_unprobed_sibling_to_customer_traffic(monkeypatch):
    provider = _provider("batched-recovery", Provider.HealthState.DEGRADED)
    models = [_model(provider, f"model-{index}") for index in range(6)]
    bad = {
        model.pk: FakeAdapter(
            error=ProviderError("missing", code="model_not_found", retryable=False)
        )
        for model in models[:4]
    }
    good = {model.pk: FakeAdapter() for model in models[4:]}
    health_adapter = FakeAdapter()
    success = Mock()
    failure = Mock()

    def adapter_for(model, **kwargs):
        # Provider metadata health may use any model. Inference probes below are
        # distinguished by the adapter's generate() call.
        adapter = bad.get(model.pk) or good.get(model.pk) or health_adapter
        return adapter

    monkeypatch.setattr(dispatch, "adapter_for", adapter_for)
    monkeypatch.setattr(reliability, "record_success", success)
    monkeypatch.setattr(reliability, "record_failure", failure)

    first = reliability.check_provider(provider)

    assert first.healthy is False
    assert first.error_code == "recovery_batch_pending"
    success.assert_not_called()
    failure.assert_not_called()
    assert sum(adapter.generate_calls for adapter in bad.values()) == 4
    assert sum(adapter.generate_calls for adapter in good.values()) == 0
    assert all(not model_quarantine.model_runtime_available(model) for model in models[:4])
    assert all(model_quarantine.model_runtime_available(model) for model in models[4:])
    snapshot = ProviderHealthSnapshot.objects.filter(provider=provider).latest("checked_at")
    assert snapshot.healthy is False
    assert snapshot.error_code == "recovery_batch_pending"

    second = reliability.check_provider(provider)

    assert second.healthy is True
    assert sum(adapter.generate_calls for adapter in bad.values()) == 4
    assert good[models[4].pk].generate_calls == 1
    success.assert_called_once()
    failure.assert_not_called()


@pytest.mark.django_db
def test_provider_transport_stays_healthy_when_models_are_not_published(monkeypatch):
    provider = _provider("unpublished-models", Provider.HealthState.DEGRADED)
    model = _model(provider, "unpublished-model")
    model.enabled = False
    model.save(update_fields=["enabled"])
    adapter = FakeAdapter()
    success = Mock()
    failure = Mock()

    monkeypatch.setattr(dispatch, "adapter_for", lambda selected, **_kwargs: adapter)
    monkeypatch.setattr(reliability, "record_success", success)
    monkeypatch.setattr(reliability, "record_failure", failure)

    health = reliability.check_provider(provider)

    assert health.healthy is True
    assert health.error_code == "no_enabled_models"
    assert adapter.health_calls == 1
    assert adapter.generate_calls == 0
    success.assert_called_once()
    failure.assert_not_called()
    snapshot = ProviderHealthSnapshot.objects.filter(provider=provider).latest("checked_at")
    assert snapshot.healthy is True
    assert snapshot.error_code == "no_enabled_models"
