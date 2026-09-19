from decimal import Decimal

import pytest
from django.test import override_settings

from apps.accounts.models import User

from .models import Payment, RefundRequest
from .services import (
    approve_refund_request,
    apply_payment_status,
    create_refund_request,
    reject_refund_request,
)


class RefundClient:
    def __init__(self):
        self.refund_status = "pending"

    def create_refund(self, payload, idempotency_key):
        return {
            "id": f"refund-{idempotency_key[-12:]}",
            "payment_id": payload["payment_id"],
            "status": self.refund_status,
            "amount": payload["amount"],
        }


def _paid_user(username="refund-request-user"):
    user = User.objects.create_user(username=username, email=f"{username}@example.com")
    payment = Payment.objects.create(
        user=user,
        provider_payment_id=f"pay-{username}",
        idempotency_key=f"topup-{username}",
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
def test_refund_request_holds_balance_until_admin_decision():
    user, payment = _paid_user()
    request = create_refund_request(
        user=user,
        payment=payment,
        amount="40.00",
        reason="Не нужен остаток",
    )
    user.wallet.refresh_from_db()
    assert request.status == RefundRequest.Status.PENDING
    assert user.wallet.available_rub == Decimal("60.0000")
    assert user.wallet.paid_rub == Decimal("60.0000")

    reject_refund_request(refund_request=request, admin_comment="Возврат не подтверждён")
    user.wallet.refresh_from_db()
    request.refresh_from_db()
    assert request.status == RefundRequest.Status.REJECTED
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.paid_rub == Decimal("100.0000")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_approved_refund_does_not_debit_held_balance_twice():
    user, payment = _paid_user("refund-approved-user")
    request = create_refund_request(user=user, payment=payment, amount="40.00", reason="return")
    user.wallet.refresh_from_db()
    assert user.wallet.paid_rub == Decimal("60.0000")

    request = approve_refund_request(
        refund_request=request,
        admin_comment="Одобрено",
        client=RefundClient(),
    )
    user.wallet.refresh_from_db()
    assert request.status == RefundRequest.Status.PROCESSING
    assert request.refund_id is not None
    assert user.wallet.available_rub == Decimal("60.0000")
    assert user.wallet.paid_rub == Decimal("60.0000")


@pytest.mark.django_db(transaction=True)
@override_settings(PAYMENTS_ENABLED=True, PAYMENTS_LIVE_ENABLED=False)
def test_pending_refund_requests_cannot_exceed_original_payment():
    user, payment = _paid_user("refund-limit-user")
    create_refund_request(user=user, payment=payment, amount="70.00", reason="first")
    with pytest.raises(Exception):
        create_refund_request(user=user, payment=payment, amount="40.00", reason="second")
