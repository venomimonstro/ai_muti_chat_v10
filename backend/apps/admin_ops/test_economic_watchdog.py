from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Notification, User
from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve

from .tasks import economic_safety_watch_task


@pytest.mark.django_db
def test_economic_watchdog_passes_without_creating_admin_warning():
    admin = User.objects.create_user(
        username="economic-admin-ok",
        email="economic-admin-ok@example.test",
        password="password123",
        role=User.Role.PLATFORM_ADMIN,
    )
    user = User.objects.create_user(
        username="economic-user-ok",
        email="economic-user-ok@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "economic-ok")

    result = economic_safety_watch_task()

    assert "ECONOMIC SAFETY: PASS" in result
    assert not Notification.objects.filter(
        user=admin,
        dedupe_key__startswith="economic-safety:",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_economic_watchdog_reports_stale_reservation_as_warning_without_false_page():
    admin = User.objects.create_user(
        username="economic-admin-stale",
        email="economic-admin-stale@example.test",
        password="password123",
        role=User.Role.PLATFORM_ADMIN,
    )
    user = User.objects.create_user(
        username="economic-user-stale",
        email="economic-user-stale@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "economic-stale")
    reserve(user, Decimal("10"), "economic-watchdog:stale")
    BalanceReservation.objects.filter(idempotency_key="economic-watchdog:stale").update(
        created_at=timezone.now() - timedelta(hours=2)
    )

    result = economic_safety_watch_task()

    assert "WARN: stale_active_reservations=1" in result
    assert not Notification.objects.filter(
        user=admin,
        dedupe_key__startswith="economic-safety:",
    ).exists()
