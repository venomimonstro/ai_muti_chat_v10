from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User

from .models import AdminBalanceAdjustment, LedgerEntry
from .services import admin_adjust_balance, credit


@pytest.mark.django_db(transaction=True)
def test_admin_credit_is_promo_and_auditable():
    admin = User.objects.create_user(username="admin-adjust", is_staff=True)
    user = User.objects.create_user(username="adjust-target")
    adjustment = admin_adjust_balance(
        target_user=user,
        admin=admin,
        direction=AdminBalanceAdjustment.Direction.CREDIT,
        amount=Decimal("50"),
        comment="Компенсация за сбой",
        idempotency_key="credit-1",
    )
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("50.0000")
    assert user.wallet.promo_rub == Decimal("50.0000")
    assert user.wallet.paid_rub == Decimal("0.0000")
    assert adjustment.ledger_entry.kind == LedgerEntry.Kind.ADJUSTMENT
    adjustment.comment = "changed"
    with pytest.raises(ValidationError):
        adjustment.save()


@pytest.mark.django_db(transaction=True)
def test_admin_debit_consumes_promo_before_paid():
    admin = User.objects.create_user(username="admin-adjust-debit", is_staff=True)
    user = User.objects.create_user(username="adjust-debit-target")
    credit(user, Decimal("100"), "test", "paid", bucket="paid")
    credit(user, Decimal("25"), "test", "promo", bucket="promo")
    admin_adjust_balance(
        target_user=user,
        admin=admin,
        direction=AdminBalanceAdjustment.Direction.DEBIT,
        amount=Decimal("30"),
        comment="Корректировка ошибочного начисления",
        idempotency_key="debit-1",
    )
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("95.0000")
    assert user.wallet.promo_rub == Decimal("0.0000")
    assert user.wallet.paid_rub == Decimal("95.0000")


@pytest.mark.django_db(transaction=True)
def test_admin_adjustment_requires_comment_key_and_cannot_overdraw():
    admin = User.objects.create_user(username="admin-adjust-guard", is_staff=True)
    user = User.objects.create_user(username="adjust-guard-target")
    with pytest.raises(ValidationError):
        admin_adjust_balance(
            target_user=user,
            admin=admin,
            direction=AdminBalanceAdjustment.Direction.CREDIT,
            amount=Decimal("10"),
            comment="",
            idempotency_key="guard-comment",
        )
    with pytest.raises(ValidationError):
        admin_adjust_balance(
            target_user=user,
            admin=admin,
            direction=AdminBalanceAdjustment.Direction.CREDIT,
            amount=Decimal("10"),
            comment="Компенсация",
            idempotency_key="",
        )
    with pytest.raises(ValidationError):
        admin_adjust_balance(
            target_user=user,
            admin=admin,
            direction=AdminBalanceAdjustment.Direction.DEBIT,
            amount=Decimal("10"),
            comment="Ручное списание",
            idempotency_key="guard-debit",
        )


@pytest.mark.django_db(transaction=True)
def test_admin_adjustment_retry_is_idempotent():
    admin = User.objects.create_user(username="admin-adjust-idempotent", is_staff=True)
    user = User.objects.create_user(username="adjust-idempotent-target")
    first = admin_adjust_balance(
        target_user=user,
        admin=admin,
        direction=AdminBalanceAdjustment.Direction.CREDIT,
        amount=Decimal("50"),
        comment="Компенсация за сбой",
        idempotency_key="same-operation",
    )
    second = admin_adjust_balance(
        target_user=user,
        admin=admin,
        direction=AdminBalanceAdjustment.Direction.CREDIT,
        amount=Decimal("50"),
        comment="Компенсация за сбой",
        idempotency_key="same-operation",
    )
    user.wallet.refresh_from_db()
    assert first.id == second.id
    assert user.wallet.available_rub == Decimal("50.0000")
    assert AdminBalanceAdjustment.objects.filter(wallet=user.wallet).count() == 1
