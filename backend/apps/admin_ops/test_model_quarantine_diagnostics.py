from decimal import Decimal

import pytest
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.model_quarantine import quarantine_model
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion

from .diagnostics_views import _model_readiness


@pytest.mark.django_db
def test_diagnostics_expose_quarantined_model_without_marking_provider_down():
    provider = Provider.objects.create(
        slug="diagnostics-quarantine-echo",
        name="Diagnostics Quarantine Echo",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    model = AIModel.objects.create(
        provider=provider,
        slug="diagnostics-quarantine-model",
        display_name="Diagnostics quarantine model",
        upstream_model="diagnostics-quarantine-model",
        enabled=True,
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=1024,
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    quarantine_model(
        model,
        ProviderError("model removed", code="model_not_found", retryable=False),
    )

    row = next(item for item in _model_readiness() if item["model"] == model.slug)

    assert row["ready"] is False
    assert row["provider_health"] == Provider.HealthState.HEALTHY
    assert row["model_quarantined"] is True
    assert row["model_quarantine_error"] == "model_not_found"
    assert "model_quarantined" in row["reasons"]
    assert "provider_unavailable" not in row["reasons"]
