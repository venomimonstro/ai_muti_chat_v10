from types import SimpleNamespace
from unittest.mock import patch

from apps.ai_registry import client_readiness, reliability, serializers as registry_serializers, views
from apps.chat import serializers as chat_serializers


def test_minimum_funding_guard_is_installed_on_all_customer_surfaces():
    assert getattr(reliability.model_client_ready, "_ai_workspace_minimum_funding", False) is True
    assert views.model_client_ready is reliability.model_client_ready
    assert registry_serializers.model_client_ready is reliability.model_client_ready
    assert chat_serializers.model_client_ready is reliability.model_client_ready


def test_minimum_inference_capacity_rejects_positive_but_unusable_balance():
    provider = SimpleNamespace(slug="provider-a")
    model = SimpleNamespace(slug="model-a", provider=provider)
    value = SimpleNamespace()

    with (
        patch.object(client_readiness, "minimum_inference_fundable", wraps=client_readiness.minimum_inference_fundable),
        patch("apps.billing.pricing.active_price", return_value=object()),
        patch("apps.billing.pricing.quote", return_value=value),
        patch("apps.billing.pricing.require_margin", return_value=value),
        patch("apps.procurement.readiness.quote_has_procurement_capacity", return_value=False) as capacity,
    ):
        assert client_readiness.minimum_inference_fundable(model) is False

    capacity.assert_called_once_with(provider, value)
