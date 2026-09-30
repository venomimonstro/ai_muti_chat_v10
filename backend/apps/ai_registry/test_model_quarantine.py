from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.billing.models import PriceVersion

from . import adapters, dispatch, reliability, router
from .adapters import ProviderError, ProviderResult
from .model_quarantine import (
    install,
    model_runtime_available,
    quarantine_model,
    recover_model,
    recover_quarantined_models,
)
from .models import AIModel, Provider, ReliabilityIncident


def _install_runtime():
    install(
        dispatch_module=dispatch,
        adapters_module=adapters,
        reliability_module=reliability,
        router_module=router,
    )


def _model(provider, slug):
    model = AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug,
        upstream_model=slug,
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=1024,
    )
    PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    return model


@pytest.mark.django_db
def test_model_not_found_quarantines_only_failing_model():
    _install_runtime()
    provider = Provider.objects.create(
        slug="quarantine-echo",
        name="Quarantine Echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
    )
    broken = _model(provider, "quarantine-broken")
    sibling = _model(provider, "quarantine-sibling")
    adapter = SimpleNamespace(_ai_workspace_model_slug=broken.slug, _ai_workspace_probe_mode=False)

    reliability.record_failure(
        provider,
        ProviderError("model removed", code="model_not_found", retryable=False),
        adapter=adapter,
    )

    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.HEALTHY
    assert provider.consecutive_failures == 0
    assert model_runtime_available(broken) is False
    assert reliability.model_client_ready(broken) is False
    assert reliability.model_client_ready(sibling) is True
    incident = ReliabilityIncident.objects.get(
        provider=provider,
        state=ReliabilityIncident.State.OPEN,
        details__scope="model",
        details__model_slug=broken.slug,
    )
    assert incident.error_code == "model_not_found"


@pytest.mark.django_db
def test_customer_adapter_fails_locally_for_quarantined_model():
    _install_runtime()
    provider = Provider.objects.create(
        slug="quarantine-local-fail-echo",
        name="Quarantine local fail Echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
    )
    model = _model(provider, "quarantine-local-fail-model")
    quarantine_model(
        model,
        ProviderError("removed upstream", code="model_not_found", retryable=False),
    )

    adapter = dispatch.adapter_for(model)
    with pytest.raises(ProviderError) as exc_info:
        adapter.generate(
            model=model.upstream_model,
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=8,
        )

    assert exc_info.value.code == "model_not_found"
    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.HEALTHY


@pytest.mark.django_db
def test_provider_health_probe_never_quarantines_model():
    _install_runtime()
    provider = Provider.objects.create(
        slug="quarantine-provider-probe-echo",
        name="Quarantine Provider Probe Echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
    )
    model = _model(provider, "quarantine-provider-probe-model")
    adapter = SimpleNamespace(
        _ai_workspace_model_slug=model.slug,
        _ai_workspace_probe_mode=True,
    )

    reliability.record_failure(
        provider,
        ProviderError("models endpoint missing", code="gigachat_model_not_found", retryable=False),
        adapter=adapter,
    )

    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.DEGRADED
    assert model_runtime_available(model) is True


@pytest.mark.django_db
def test_router_rejects_quarantined_model_without_rejecting_sibling():
    _install_runtime()
    provider = Provider.objects.create(
        slug="quarantine-router-echo",
        name="Quarantine Router Echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
    )
    broken = _model(provider, "quarantine-router-broken")
    sibling = _model(provider, "quarantine-router-sibling")
    quarantine_model(
        broken,
        ProviderError("gone", code="model_not_found", retryable=False),
    )
    classification = router.TaskClassification(
        taxonomy="qa",
        confidence=0.9,
        required_capabilities=["text"],
        signals={},
    )

    broken_row = router._route_row(
        broken,
        classification,
        64,
        default_quality=0.55,
        unknown_latency=1500,
    )
    sibling_row = router._route_row(
        sibling,
        classification,
        64,
        default_quality=0.55,
        unknown_latency=1500,
    )

    assert broken_row["status"] == "rejected"
    assert "model_quarantined" in broken_row["reasons"]
    assert sibling_row["status"] == "eligible"


@pytest.mark.django_db
def test_generic_provider_success_does_not_recover_model_quarantine():
    _install_runtime()
    provider = Provider.objects.create(
        slug="quarantine-health-echo",
        name="Quarantine Health Echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.DEGRADED,
        consecutive_failures=1,
    )
    broken = _model(provider, "quarantine-health-broken")
    healthy = _model(provider, "quarantine-health-sibling")
    quarantine_model(
        broken,
        ProviderError("gone", code="model_not_found", retryable=False),
    )

    reliability.record_success(
        provider,
        12,
        adapter=SimpleNamespace(_ai_workspace_model_slug=healthy.slug),
    )

    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.HEALTHY
    assert model_runtime_available(broken) is False
    assert model_runtime_available(healthy) is True


@pytest.mark.django_db
def test_provider_outage_incident_is_not_masked_by_model_quarantine():
    _install_runtime()
    provider = Provider.objects.create(
        slug="quarantine-provider-outage-echo",
        name="Quarantine Provider Outage Echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
    )
    broken = _model(provider, "quarantine-provider-outage-model")
    quarantine_model(
        broken,
        ProviderError("model gone", code="model_not_found", retryable=False),
    )

    reliability.record_failure(
        provider,
        ProviderError("credential rejected", code="authentication_error", retryable=False),
        adapter=SimpleNamespace(
            _ai_workspace_model_slug=broken.slug,
            _ai_workspace_probe_mode=False,
        ),
    )

    provider.refresh_from_db()
    assert provider.health_state == Provider.HealthState.OPEN
    assert model_runtime_available(broken) is False
    model_incident = ReliabilityIncident.objects.get(
        provider=provider,
        state=ReliabilityIncident.State.OPEN,
        details__scope="model",
        details__model_slug=broken.slug,
    )
    provider_incident = ReliabilityIncident.objects.get(
        provider=provider,
        state=ReliabilityIncident.State.OPEN,
        details__scope="provider",
    )
    assert model_incident.error_code == "model_not_found"
    assert provider_incident.error_code == "authentication_error"


@pytest.mark.django_db
def test_recover_model_reopens_only_that_model():
    provider = Provider.objects.create(
        slug="quarantine-recovery-echo",
        name="Quarantine Recovery Echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
    )
    first = _model(provider, "quarantine-recovery-first")
    second = _model(provider, "quarantine-recovery-second")
    error = ProviderError("gone", code="model_not_found", retryable=False)
    quarantine_model(first, error)
    quarantine_model(second, error)

    assert recover_model(first) == 1
    assert model_runtime_available(first) is True
    assert model_runtime_available(second) is False


@pytest.mark.django_db
def test_background_probe_recovers_quarantined_model(monkeypatch):
    _install_runtime()
    provider = Provider.objects.create(
        slug="quarantine-probe-echo",
        name="Quarantine Probe Echo",
        adapter_type=Provider.AdapterType.ECHO,
        health_state=Provider.HealthState.HEALTHY,
    )
    model = _model(provider, "quarantine-probe-model")
    quarantine_model(
        model,
        ProviderError("temporarily gone", code="model_not_found", retryable=False),
    )

    fake = SimpleNamespace(
        _ai_workspace_model_slug=model.slug,
        _ai_workspace_probe_mode=True,
        generate=lambda **_kwargs: ProviderResult(
            text="OK",
            input_tokens=2,
            output_tokens=1,
            provider_request_id="quarantine-probe-ok",
        ),
    )
    monkeypatch.setattr(dispatch, "adapter_for", lambda _model, **_kwargs: fake)

    result = recover_quarantined_models(limit=4)

    assert result["checked"] == 1
    assert result["recovered"] == 1
    assert model_runtime_available(model) is True
