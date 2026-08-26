from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.accounts.models import User

from .models import Payment, PaymentEvent, Refund
from .services import create_refund, create_topup, process_webhook


class FakeYooKassa:
    def __init__(self, payment_id="pay_remote_1", refund_id="refund_remote_1"):
        self.payment_calls = 0
        self.refund_calls = 0
        self.payment_status = "pending"
        self.refund_status = "pending"
        self.payment_amount = "100.00"
        self.payment_id = payment_id
        self.refund_id = refund_id

    def create_payment(self, payload, idempotency_key):
        self.payment_calls += 1
        return {
            "id": self.payment_id,
            "status": "pending",
            "amount": payload["amount"],
            "confirmation": {"confirmation_url": "https://yookassa.test/confirm"},
            "metadata": payload["metadata"],
        }

    def get_payment(self, payment_id):
        payment = Payment.objects.get(provider_payment_id=payment_id)
        return {
            "id": payment_id,
            "status": self.payment_status,
            "amount": {"value": self.payment_amount, "currency": "RUB"},
            "income_amount": {"value": "97.00", "currency": "RUB"},
            "metadata": {"payment_id": str(payment.id), "user_id": str(payment.user_id)},
        }

    def create_refund(self, payload, idempotency_key):
        self.refund_calls += 1
        return {
            "id": self.refund_id,
            "payment_id": payload["payment_id"],
            "status": self.refund_status,
            "amount": payload["amount"],
        }

    def get_refund(self, refund_id):
        refund = Refund.objects.get(provider_refund_id=refund_id)
        return {
            "id": refund_id,
            "payment_id": refund.payment.provider_payment_id,
            "status": self.refund_status,
            "amount": {"value": f"{refund.amount_rub:.2f}", "currency": "RUB"},
        }


def payment_event(name="payment.succeeded"):
    return {
        "type": "notification",
        "event": name,
        "object": {"id": "pay_remote_1", "status": name.split(".")[-1]},
    }


@pytest.fixture
def user():
    return User.objects.create_user(
        username="payer", email="payer@example.com", password="password123"
    )


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_payment_create_and_webhook_are_idempotent(user):
    client = FakeYooKassa()
    first = create_topup(user=user, amount="100.00", idempotency_key="topup-one", client=client)
    second = create_topup(user=user, amount="100.00", idempotency_key="topup-one", client=client)
    assert first.id == second.id
    assert client.payment_calls == 1
    client.payment_status = "succeeded"
    assert process_webhook(payment_event(), client=client) == "credited"
    assert process_webhook(payment_event(), client=client) == "credited"
    first.refresh_from_db()
    user.wallet.refresh_from_db()
    assert first.status == Payment.Status.SUCCEEDED
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.paid_rub == Decimal("100.0000")
    assert PaymentEvent.objects.count() == 1
    assert first.cost.net_received_rub == Decimal("97.00")
    assert first.cost.acquiring_fee_rub == Decimal("3.00")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_forged_webhook_does_not_credit_without_authoritative_status(user):
    client = FakeYooKassa()
    payment = create_topup(
        user=user, amount="100.00", idempotency_key="topup-forged", client=client
    )
    assert process_webhook(payment_event(), client=client) == "ignored"
    payment.refresh_from_db()
    assert payment.credited_at is None
    assert not hasattr(user, "wallet")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_amount_mismatch_is_rejected(user):
    client = FakeYooKassa()
    create_topup(user=user, amount="100.00", idempotency_key="topup-mismatch", client=client)
    client.payment_status = "succeeded"
    client.payment_amount = "999.00"
    with pytest.raises(ValidationError):
        process_webhook(payment_event(), client=client)
    assert not hasattr(user, "wallet")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_reordered_canceled_event_cannot_downgrade_success(user):
    client = FakeYooKassa()
    payment = create_topup(user=user, amount="100.00", idempotency_key="topup-order", client=client)
    client.payment_status = "succeeded"
    process_webhook(payment_event(), client=client)
    client.payment_status = "canceled"
    process_webhook(payment_event("payment.canceled"), client=client)
    payment.refresh_from_db()
    user.wallet.refresh_from_db()
    assert payment.status == Payment.Status.SUCCEEDED
    assert user.wallet.paid_rub == Decimal("100.0000")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_refund_debits_paid_balance_once(user):
    client = FakeYooKassa()
    payment = create_topup(
        user=user, amount="100.00", idempotency_key="topup-refund", client=client
    )
    client.payment_status = "succeeded"
    process_webhook(payment_event(), client=client)
    refund = create_refund(
        payment=payment, amount="40.00", idempotency_key="refund-one", client=client
    )
    same = create_refund(
        payment=payment, amount="40.00", idempotency_key="refund-one", client=client
    )
    assert refund.id == same.id
    assert client.refund_calls == 1
    user.wallet.refresh_from_db()
    assert user.wallet.paid_rub == Decimal("60.0000")
    client.refund_status = "succeeded"
    payload = {
        "type": "notification",
        "event": "refund.succeeded",
        "object": {"id": "refund_remote_1", "status": "succeeded"},
    }
    process_webhook(payload, client=client)
    process_webhook(payload, client=client)
    refund.refresh_from_db()
    user.wallet.refresh_from_db()
    assert refund.status == Refund.Status.SUCCEEDED
    assert user.wallet.paid_rub == Decimal("60.0000")


@pytest.mark.django_db
@override_settings(
    PAYMENTS_ENABLED=True,
    PAYMENTS_LIVE_ENABLED=True,
    PAYMENTS_FISCALIZATION_MODE="disabled",
)
def test_live_payments_blocked_without_fiscalization(user):
    with pytest.raises(ValidationError):
        create_topup(
            user=user,
            amount="100.00",
            idempotency_key="live-without-receipt",
            client=FakeYooKassa(),
        )


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_payment_idempotency_is_scoped_per_user(user):
    other = User.objects.create_user(username="payer-two", email="payer-two@example.com")
    first = create_topup(
        user=user,
        amount="100.00",
        idempotency_key="same-client-key",
        client=FakeYooKassa(payment_id="pay_scope_1"),
    )
    second = create_topup(
        user=other,
        amount="100.00",
        idempotency_key="same-client-key",
        client=FakeYooKassa(payment_id="pay_scope_2"),
    )
    assert first.id != second.id


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_reconcile_skips_payments_without_provider_id(user):
    from .services import reconcile_open_payments

    payment = Payment.objects.create(
        user=user,
        amount_rub=Decimal("100.00"),
        idempotency_key="orphan-payment",
        status=Payment.Status.CREATED,
    )
    run = reconcile_open_payments(client=FakeYooKassa(payment_id="unused"))
    assert run.error_count == 0
    payment.refresh_from_db()
    assert payment.status == Payment.Status.CREATED
    assert payment.provider_payment_id is None
