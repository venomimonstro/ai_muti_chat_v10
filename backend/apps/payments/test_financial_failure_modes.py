from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.accounts.models import User

from .external_refunds import register_unknown_succeeded_refund
from .models import Payment, Refund, RefundRequest
from .provider import PaymentProviderError
from .services import (
    approve_refund_request,
    apply_payment_status,
    create_refund,
    create_refund_request,
    create_topup,
)


class TimeoutPaymentClient:
    def __init__(self):
        self.keys = []

    def create_payment(self, payload, idempotency_key):
        self.keys.append(idempotency_key)
        raise PaymentProviderError("timeout")


class GoodPaymentClient:
    def __init__(self):
        self.keys = []

    def create_payment(self, payload, idempotency_key):
        self.keys.append(idempotency_key)
        return {
            "id": "pay-after-timeout",
            "status": "pending",
            "amount": payload["amount"],
            "confirmation": {"confirmation_url": "https://example.test/pay"},
            "metadata": payload["metadata"],
        }


class TimeoutRefundClient:
    def __init__(self):
        self.keys = []

    def create_refund(self, payload, idempotency_key):
        self.keys.append(idempotency_key)
        raise PaymentProviderError("timeout")


class GoodRefundClient:
    def __init__(self, refund_id="refund-after-timeout", status="pending"):
        self.refund_id = refund_id
        self.status = status
        self.keys = []

    def create_refund(self, payload, idempotency_key):
        self.keys.append(idempotency_key)
        return {
            "id": self.refund_id,
            "payment_id": payload["payment_id"],
            "status": self.status,
            "amount": payload["amount"],
        }


class RefundWebhookClient:
    def __init__(self, *, payment_id, amount="40.00", refund_id="refund-webhook-after-timeout"):
        self.payment_id = payment_id
        self.amount = amount
        self.refund_id = refund_id

    def get_refund(self, refund_id):
        assert refund_id == self.refund_id
        return {
            "id": refund_id,
            "payment_id": self.payment_id,
            "status": "succeeded",
            "amount": {"value": self.amount, "currency": "RUB"},
        }


def _paid_payment(user, *, amount="100.00", suffix="one"):
    payment = Payment.objects.create(
        user=user,
        provider_payment_id=f"pay-{suffix}",
        idempotency_key=f"topup-{suffix}",
        amount_rub=Decimal(amount),
        status=Payment.Status.PENDING,
        return_url="https://example.test/return",
        description="test",
    )
    apply_payment_status(
        payment.id,
        {
            "id": payment.provider_payment_id,
            "status": "succeeded",
            "amount": {"value": amount, "currency": "RUB"},
            "metadata": {"payment_id": str(payment.id)},
        },
    )
    payment.refresh_from_db()
    return payment


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_topup_timeout_keeps_local_operation_for_same_key_retry():
    user = User.objects.create_user(username="topup-timeout", email="topup-timeout@example.test")
    timeout_client = TimeoutPaymentClient()
    with pytest.raises(PaymentProviderError):
        create_topup(
            user=user,
            amount="100.00",
            idempotency_key="stable-topup-key",
            client=timeout_client,
        )

    payment = Payment.objects.get(user=user, idempotency_key="stable-topup-key")
    assert payment.provider_payment_id is None
    assert payment.status == Payment.Status.CREATED
    assert payment.last_error == "PaymentProviderError"

    good_client = GoodPaymentClient()
    retried = create_topup(
        user=user,
        amount="100.00",
        idempotency_key="stable-topup-key",
        client=good_client,
    )
    assert retried.id == payment.id
    assert retried.provider_payment_id == "pay-after-timeout"
    assert timeout_client.keys == good_client.keys
    assert not hasattr(user, "wallet")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_direct_refund_timeout_keeps_wallet_hold_and_retry_does_not_debit_twice():
    user = User.objects.create_user(username="refund-timeout", email="refund-timeout@example.test")
    payment = _paid_payment(user, suffix="refund-timeout")
    with pytest.raises(PaymentProviderError):
        create_refund(
            payment=payment,
            amount="40.00",
            idempotency_key="stable-refund-key",
            client=TimeoutRefundClient(),
        )

    refund = Refund.objects.get(payment=payment, idempotency_key="stable-refund-key")
    assert refund.provider_refund_id is None
    assert refund.wallet_debited_at is not None
    user.wallet.refresh_from_db()
    assert user.wallet.paid_rub == Decimal("60.0000")

    retried = create_refund(
        payment=payment,
        amount="40.00",
        idempotency_key="stable-refund-key",
        client=GoodRefundClient(),
    )
    assert retried.id == refund.id
    user.wallet.refresh_from_db()
    assert user.wallet.paid_rub == Decimal("60.0000")
    assert Refund.objects.filter(payment=payment).count() == 1


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_refund_webhook_after_timeout_attaches_local_refund_without_second_wallet_debit():
    user = User.objects.create_user(username="refund-webhook-timeout", email="refund-webhook-timeout@example.test")
    payment = _paid_payment(user, suffix="refund-webhook-timeout")
    with pytest.raises(PaymentProviderError):
        create_refund(
            payment=payment,
            amount="40.00",
            idempotency_key="webhook-timeout-key",
            client=TimeoutRefundClient(),
        )
    user.wallet.refresh_from_db()
    assert user.wallet.paid_rub == Decimal("60.0000")

    provider_refund_id = "refund-webhook-after-timeout"
    recovered = register_unknown_succeeded_refund(
        {
            "type": "notification",
            "event": "refund.succeeded",
            "object": {"id": provider_refund_id},
        },
        client=RefundWebhookClient(
            payment_id=payment.provider_payment_id,
            refund_id=provider_refund_id,
        ),
    )
    assert recovered is True
    refund = Refund.objects.get(payment=payment, idempotency_key="webhook-timeout-key")
    assert refund.provider_refund_id == provider_refund_id
    assert refund.status == Refund.Status.SUCCEEDED
    assert Refund.objects.filter(payment=payment).count() == 1
    user.wallet.refresh_from_db()
    assert user.wallet.paid_rub == Decimal("60.0000")
    assert user.wallet.available_rub == Decimal("60.0000")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_refund_request_timeout_never_restores_held_money_until_provider_is_known():
    user = User.objects.create_user(username="request-timeout", email="request-timeout@example.test")
    payment = _paid_payment(user, suffix="request-timeout")
    request = create_refund_request(
        user=user,
        payment=payment,
        amount="40.00",
        reason="unused balance",
    )
    with pytest.raises(PaymentProviderError):
        approve_refund_request(
            refund_request=request,
            admin_comment="approved",
            client=TimeoutRefundClient(),
        )

    request.refresh_from_db()
    user.wallet.refresh_from_db()
    assert request.status == RefundRequest.Status.PROCESSING
    assert request.refund_id is not None
    assert user.wallet.paid_rub == Decimal("60.0000")

    request = approve_refund_request(
        refund_request=request,
        admin_comment="retry same operation",
        client=GoodRefundClient(refund_id="request-refund-after-timeout"),
    )
    user.wallet.refresh_from_db()
    assert request.status == RefundRequest.Status.PROCESSING
    assert user.wallet.paid_rub == Decimal("60.0000")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_direct_refund_cannot_overcommit_payment_with_customer_hold():
    user = User.objects.create_user(username="refund-overcommit", email="refund-overcommit@example.test")
    payment = _paid_payment(user, suffix="primary")
    _paid_payment(user, suffix="other")
    create_refund_request(
        user=user,
        payment=payment,
        amount="70.00",
        reason="hold most of first payment",
    )
    user.wallet.refresh_from_db()
    assert user.wallet.paid_rub == Decimal("130.0000")

    with pytest.raises(ValidationError):
        create_refund(
            payment=payment,
            amount="40.00",
            idempotency_key="must-not-overcommit",
            client=GoodRefundClient(refund_id="must-not-exist"),
        )
    assert not Refund.objects.filter(payment=payment, idempotency_key="must-not-overcommit").exists()
    user.wallet.refresh_from_db()
    assert user.wallet.paid_rub == Decimal("130.0000")
