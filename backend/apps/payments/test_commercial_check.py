import io
import json
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from .models import PaymentFeeVersion, ReconciliationRun


@pytest.mark.django_db
@override_settings(
    PAYMENTS_ENABLED=True,
    PAYMENTS_LIVE_ENABLED=True,
    YOOKASSA_SHOP_ID="shop",
    YOOKASSA_SECRET_KEY="secret",
    PAYMENT_RETURN_URL="https://example.test/billing/return",
    PAYMENTS_FISCALIZATION_MODE="provider_receipt",
    PAYMENTS_VAT_CODE=1,
    PAYMENT_MIN_RUB="100.00",
    PAYMENT_MAX_RUB="100000.00",
)
def test_payment_commercial_check_accepts_string_money_limits():
    PaymentFeeVersion.objects.create(
        provider="yookassa",
        payment_method="unknown",
        percent=Decimal("3.5000"),
        fixed_rub=Decimal("0.00"),
        effective_from=timezone.now(),
        active=True,
    )
    ReconciliationRun.objects.create(
        status=ReconciliationRun.Status.SUCCEEDED,
        finished_at=timezone.now(),
    )
    output = io.StringIO()
    call_command(
        "payment_commercial_check",
        "--require-reconciliation",
        "--json",
        stdout=output,
    )
    payload = json.loads(output.getvalue())
    assert payload["passed"] is True


@pytest.mark.django_db
@override_settings(
    PAYMENTS_ENABLED=True,
    PAYMENTS_LIVE_ENABLED=True,
    YOOKASSA_SHOP_ID="shop",
    YOOKASSA_SECRET_KEY="secret",
    PAYMENT_RETURN_URL="https://example.test/billing/return",
    PAYMENTS_FISCALIZATION_MODE="provider_receipt",
    PAYMENTS_VAT_CODE=1,
    PAYMENT_MIN_RUB="not-money",
    PAYMENT_MAX_RUB="100000.00",
)
def test_payment_commercial_check_blocks_invalid_money_limit_without_type_error():
    output = io.StringIO()
    with pytest.raises(CommandError):
        call_command("payment_commercial_check", "--json", stdout=output)
    payload = json.loads(output.getvalue())
    limits = next(item for item in payload["checks"] if item["name"] == "payment_limits")
    assert limits["passed"] is False
