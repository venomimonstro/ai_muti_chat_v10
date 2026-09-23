from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.billing.services import credit, reserve, settle

from .models import Payment, Refund, RefundRequest
from .serializers import PaymentSerializer


@pytest.fixture
def user(db):
    return User.objects.create_user(username="refund-balance", email="refund-balance@example.test")


def payment(user, *, status=Payment.Status.SUCCEEDED):
    return Payment.objects.create(
        user=user,
        provider_payment_id="pay-refundable" if status == Payment.Status.SUCCEEDED else None,
        idempotency_key=f"payment-{status}",
        amount_rub=Decimal("100.00"),
        status=status,
        return_url="https://example.test/return",
        description="test",
    )


def fund(user, amount="100.00"):
    credit(user, Decimal(amount), "payment", "serializer-funding", bucket="paid")


@pytest.mark.django_db
def test_payment_serializer_reports_only_currently_refundable_amount(user):
    fund(user)
    item = payment(user)
    Refund.objects.create(
        payment=item,
        provider_refund_id="refund-pending",
        idempotency_key="refund-1",
        amount_rub=Decimal("30.00"),
        status=Refund.Status.PENDING,
    )
    RefundRequest.objects.create(
        user=user,
        payment=item,
        idempotency_key="request-1",
        amount_rub=Decimal("20.00"),
        status=RefundRequest.Status.PENDING,
    )

    assert PaymentSerializer(item).data["refundable_rub"] == "50.00"


@pytest.mark.django_db
def test_canceled_refund_and_rejected_request_do_not_reduce_refundable_amount(user):
    fund(user)
    item = payment(user)
    Refund.objects.create(
        payment=item,
        provider_refund_id="refund-canceled",
        idempotency_key="refund-2",
        amount_rub=Decimal("30.00"),
        status=Refund.Status.CANCELED,
    )
    RefundRequest.objects.create(
        user=user,
        payment=item,
        idempotency_key="request-2",
        amount_rub=Decimal("20.00"),
        status=RefundRequest.Status.REJECTED,
    )

    assert PaymentSerializer(item).data["refundable_rub"] == "100.00"


@pytest.mark.django_db
def test_refundable_amount_is_capped_by_unused_paid_wallet_balance(user):
    fund(user)
    item = payment(user)
    reservation = reserve(user, Decimal("80.00"), "generation:spent-before-refund")
    settle(reservation.id, Decimal("80.00"))

    assert PaymentSerializer(item).data["refundable_rub"] == "20.00"


@pytest.mark.django_db
def test_non_succeeded_payment_has_zero_refundable_amount(user):
    fund(user)
    item = payment(user, status=Payment.Status.PENDING)
    assert PaymentSerializer(item).data["refundable_rub"] == "0.00"
