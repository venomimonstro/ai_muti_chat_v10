from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from . import customer_capacity


def _modules(raw_prepare, raw_preview):
    streaming = SimpleNamespace(
        quote_has_procurement_capacity=lambda _provider, _value: True,
        prepare=raw_prepare,
    )
    preview = SimpleNamespace(
        quote_has_procurement_capacity=lambda _provider, _value: True,
        chat_cost_preview=raw_preview,
    )
    customer_capacity.install(
        streaming_module=streaming,
        cost_preview_module=preview,
    )
    return streaming, preview


def test_expensive_fallback_is_filtered_without_blocking_affordable_primary():
    user = object()
    cheap = SimpleNamespace(user_charge_rub="5.00")
    expensive = SimpleNamespace(user_charge_rub="25.00")
    provider = object()
    observations = {}

    def raw_prepare(**_kwargs):
        observations["cheap"] = streaming.quote_has_procurement_capacity(provider, cheap)
        observations["expensive"] = streaming.quote_has_procurement_capacity(provider, expensive)
        return "prepared"

    streaming, _preview = _modules(raw_prepare, lambda **_kwargs: {})
    with patch.object(
        customer_capacity,
        "customer_can_reserve",
        side_effect=lambda current_user, amount: current_user is user and float(amount) <= 10,
    ):
        assert streaming.prepare(user=user) == "prepared"

    assert observations == {"cheap": True, "expensive": False}


def test_preview_uses_same_customer_capacity_as_real_prepare():
    user = object()
    cheap = SimpleNamespace(user_charge_rub="4.00")
    expensive = SimpleNamespace(user_charge_rub="40.00")
    provider = object()
    observations = {}

    def raw_preview(**_kwargs):
        observations["cheap"] = preview.quote_has_procurement_capacity(provider, cheap)
        observations["expensive"] = preview.quote_has_procurement_capacity(provider, expensive)
        return {"ok": True}

    _streaming, preview = _modules(lambda **_kwargs: None, raw_preview)
    with patch.object(
        customer_capacity,
        "customer_can_reserve",
        side_effect=lambda current_user, amount: current_user is user and float(amount) <= 10,
    ):
        assert preview.chat_cost_preview(user=user) == {"ok": True}

    assert observations == {"cheap": True, "expensive": False}


def test_customer_capacity_failure_is_not_reported_as_provider_balance_failure():
    user = object()
    expensive = SimpleNamespace(user_charge_rub="25.00")
    provider = object()

    def raw_prepare(**_kwargs):
        assert streaming.quote_has_procurement_capacity(provider, expensive) is False
        raise ValidationError("Сейчас нет модели с доступным API-балансом для этого запроса")

    streaming, _preview = _modules(raw_prepare, lambda **_kwargs: {})
    with patch.object(customer_capacity, "customer_can_reserve", return_value=False):
        with pytest.raises(ValidationError) as caught:
            streaming.prepare(user=user)

    assert "балансе недостаточно средств" in str(caught.value).casefold()
    assert "api-балансом" not in str(caught.value).casefold()
