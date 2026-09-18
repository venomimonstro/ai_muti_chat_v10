from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import SupportRequest, User
from apps.billing.services import credit, release, reserve


@pytest.mark.django_db(transaction=True)
def test_customer_trust_gate_blocks_orphan_active_reservation():
    user = User.objects.create_user(
        username="trust-orphan",
        email="trust-orphan@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "trust-orphan")
    reservation = reserve(user, Decimal("10"), "trust:orphan")

    with pytest.raises(CommandError, match="orphan_active_reservations=1"):
        call_command("customer_trust_check")

    release(reservation.id)
    call_command("customer_trust_check")


@pytest.mark.django_db
def test_customer_trust_gate_blocks_unanswered_support_past_sla(monkeypatch):
    monkeypatch.setenv("SUPPORT_MAX_UNANSWERED_HOURS", "24")
    user = User.objects.create_user(
        username="trust-support",
        email="trust-support@example.test",
        password="password123",
    )
    request = SupportRequest.objects.create(
        user=user,
        subject="Баланс списался неверно",
        category=SupportRequest.Category.BILLING,
        message="Нужна проверка",
    )
    SupportRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(hours=25)
    )

    with pytest.raises(CommandError, match="support_unanswered_over_24h=1"):
        call_command("customer_trust_check")

    SupportRequest.objects.filter(pk=request.pk).update(
        status=SupportRequest.Status.RESOLVED,
        admin_reply="Проверено и решено",
        replied_at=timezone.now(),
    )
    call_command("customer_trust_check")
