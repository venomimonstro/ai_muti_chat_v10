from decimal import Decimal

import pytest

from apps.accounts.models import User

from .services import create_topup


class CapturingPaymentClient:
    def __init__(self):
        self.payload = None

    def create_payment(self, payload, _idempotency_key):
        self.payload = payload
        return {
            "id": "payment-one-time",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://pay.example.test/confirm"},
        }


@pytest.mark.django_db(transaction=True)
def test_topup_never_saves_payment_method_or_enables_recurring_charge(settings):
    settings.PAYMENTS_ENABLED = True
    settings.PAYMENTS_LIVE_ENABLED = False
    settings.PAYMENTS_FISCALIZATION_MODE = "disabled"
    settings.PAYMENT_MIN_RUB = "100.00"
    settings.PAYMENT_MAX_RUB = "100000.00"
    settings.PAYMENT_RETURN_URL = "https://example.test/app/wallet/return"
    user = User.objects.create_user(
        username="one-time-payment",
        email="one-time-payment@example.test",
        password="password123",
    )
    client = CapturingPaymentClient()

    payment = create_topup(
        user=user,
        amount=Decimal("1000"),
        idempotency_key="one-time-contract",
        client=client,
    )

    assert payment.amount_rub == Decimal("1000.00")
    assert client.payload is not None
    assert "save_payment_method" not in client.payload
    assert "payment_method_id" not in client.payload
    assert "recurring" not in client.payload
    assert client.payload["confirmation"]["type"] == "redirect"
