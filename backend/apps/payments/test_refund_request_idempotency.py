from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.accounts.models import User

from .models import Payment, RefundRequest
from .refund_request_idempotency import create_refund_request_idempotent
from .services import apply_payment_status, reject_refund_request


def _paid_user():
    user = User.objects.create_user(
        username="refund-request-idempotent",
        email="refund-request-idempotent@example.test",
    )
    payment = Payment.objects.create(
        user=user,
        provider_payment_id="pay-refund-request-idempotent",
        idempotency_key="topup-refund-request-idempotent",
        amount_rub=Decimal("100.00"),
        status=Payment.Status.PENDING,
        return_url="https://example.test/return",
        description="test",
    )
    apply_payment_status(
        payment.id,
        {
            "id": payment.provider_payment_id,
            "status": "succeeded",
            "amount": {"value": "100.00", "currency": "RUB"},
            "metadata": {"payment_id": str(payment.id)},
        },
    )
    payment.refresh_from_db()
    return user, payment


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_same_refund_request_key_holds_paid_balance_once():
    user, payment = _paid_user()
    first = create_refund_request_idempotent(
        user=user,
        payment=payment,
        amount="40.00",
        reason="unused balance",
        idempotency_key="refund-request-one",
    )
    second = create_refund_request_idempotent(
        user=user,
        payment=payment,
        amount="40.00",
        reason="unused balance",
        idempotency_key="refund-request-one",
    )
    user.wallet.refresh_from_db()
    assert first.id == second.id
    assert RefundRequest.objects.filter(user=user).count() == 1
    assert user.wallet.available_rub == Decimal("60.0000")
    assert user.wallet.paid_rub == Decimal("60.0000")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_refund_request_key_cannot_be_reused_for_different_payload():
    user, payment = _paid_user()
    create_refund_request_idempotent(
        user=user,
        payment=payment,
        amount="40.00",
        reason="unused balance",
        idempotency_key="refund-request-reuse",
    )
    with pytest.raises(ValidationError):
        create_refund_request_idempotent(
            user=user,
            payment=payment,
            amount="50.00",
            reason="different operation",
            idempotency_key="refund-request-reuse",
        )
    user.wallet.refresh_from_db()
    assert RefundRequest.objects.filter(user=user).count() == 1
    assert user.wallet.paid_rub == Decimal("60.0000")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_different_key_cannot_create_second_open_request_for_same_payment():
    user, payment = _paid_user()
    create_refund_request_idempotent(
        user=user,
        payment=payment,
        amount="20.00",
        reason="first request",
        idempotency_key="refund-request-first",
    )

    with pytest.raises(ValidationError, match="уже есть заявка"):
        create_refund_request_idempotent(
            user=user,
            payment=payment,
            amount="10.00",
            reason="browser retried after lost response",
            idempotency_key="refund-request-second",
        )

    user.wallet.refresh_from_db()
    assert RefundRequest.objects.filter(user=user, payment=payment).count() == 1
    assert user.wallet.available_rub == Decimal("80.0000")
    assert user.wallet.paid_rub == Decimal("80.0000")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_new_partial_request_allowed_after_previous_request_is_resolved():
    user, payment = _paid_user()
    first = create_refund_request_idempotent(
        user=user,
        payment=payment,
        amount="20.00",
        reason="first request",
        idempotency_key="refund-request-resolved-first",
    )
    reject_refund_request(refund_request=first, admin_comment="rejected for test")

    second = create_refund_request_idempotent(
        user=user,
        payment=payment,
        amount="10.00",
        reason="new request",
        idempotency_key="refund-request-after-resolution",
    )

    user.wallet.refresh_from_db()
    assert second.status == RefundRequest.Status.PENDING
    assert RefundRequest.objects.filter(user=user, payment=payment).count() == 2
    assert user.wallet.available_rub == Decimal("90.0000")
    assert user.wallet.paid_rub == Decimal("90.0000")
