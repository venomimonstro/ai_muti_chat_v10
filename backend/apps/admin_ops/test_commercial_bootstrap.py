from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.admin_ops.commercial_bootstrap import bootstrap_commercial_catalog, commercial_setup_status
from apps.ai_registry.models import AIModel, Provider, RoutingPolicyVersion
from apps.billing.models import FxRateSnapshot, MarginPolicyVersion, MarkupRuleVersion, PriceVersion


@pytest.mark.django_db
def test_bootstrap_is_idempotent_and_safe_by_default(monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "XAI_API_KEY",
        "OPENAI_DEFAULT_MODEL",
        "ANTHROPIC_DEFAULT_MODEL",
        "DEEPSEEK_DEFAULT_MODEL",
        "GEMINI_DEFAULT_MODEL",
        "XAI_DEFAULT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    first = bootstrap_commercial_catalog()
    second = bootstrap_commercial_catalog()

    assert first["providers"] == 5
    assert first["models"] == 5
    assert second["providers"] == 0
    assert second["models"] == 0
    assert Provider.objects.count() == 5
    assert AIModel.objects.count() == 5
    assert not Provider.objects.filter(enabled=True).exists()
    assert not AIModel.objects.filter(enabled=True).exists()
    assert RoutingPolicyVersion.objects.filter(active=True).exists()
    assert MarkupRuleVersion.objects.filter(active=True).exists()
    assert MarginPolicyVersion.objects.filter(active=True).exists()
    assert FxRateSnapshot.objects.filter(
        base_currency="RUB", quote_currency="RUB", rate=Decimal("1")
    ).exists()


@pytest.mark.django_db
def test_bootstrap_creates_version_and_price_only_when_explicitly_configured(monkeypatch):
    monkeypatch.setenv("OPENAI_DEFAULT_MODEL", "provider-model-id")
    monkeypatch.setenv("AI_PRICE_OPENAI_DEFAULT_INPUT_RUB_PER_MILLION", "100")
    monkeypatch.setenv("AI_PRICE_OPENAI_DEFAULT_OUTPUT_RUB_PER_MILLION", "200")

    bootstrap_commercial_catalog()

    model = AIModel.objects.get(slug="openai-default")
    price = PriceVersion.objects.get(model_slug="openai-default", active=True)
    assert model.current_version is not None
    assert model.current_version.exact_api_id == "provider-model-id"
    assert model.enabled is False
    assert price.input_rub_per_million == Decimal("100")
    assert price.output_rub_per_million == Decimal("200")


@pytest.mark.django_db
def test_commercial_gate_blocks_empty_catalog():
    bootstrap_commercial_catalog()
    with pytest.raises(CommandError):
        call_command("commercial_config_check")


@pytest.mark.django_db
def test_setup_status_never_returns_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")
    bootstrap_commercial_catalog()

    payload = commercial_setup_status()
    serialized = str(payload)
    assert "super-secret-value" not in serialized
    openai = next(item for item in payload["providers"] if item["slug"] == "openai")
    assert openai["credential_configured"] is True
    assert openai["credential_env"] == "OPENAI_API_KEY"
