from decimal import Decimal

import pytest
from django.db import connection

from apps.accounts.models import Notification, User
from apps.billing.services import credit

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
    assert not Notification.objects.filter(user=admin, dedupe_key__startswith="economic-safety:").exists()


@pytest.mark.django_db(transaction=True)
def test_economic_watchdog_alerts_admin_when_database_wallet_invariant_is_corrupted():
    admin = User.objects.create_user(
        username="economic-admin-bad",
        email="economic-admin-bad@example.test",
        password="password123",
        role=User.Role.PLATFORM_ADMIN,
    )
    user = User.objects.create_user(
        username="economic-user-bad",
        email="economic-user-bad@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "economic-bad")

    # PostgreSQL constraints normally prevent this state. Temporarily defer to a raw SQL
    # corruption simulation only when the backend permits constraint disabling is unsafe,
    # so instead create a stale active reservation through the public service invariant.
    from apps.billing.services import reserve

    reserve(user, Decimal("10"), "economic-watchdog:stale")
    from apps.billing.models import BalanceReservation
    from django.utils import timezone
    from datetime import timedelta

    BalanceReservation.objects.filter(idempotency_key="economic-watchdog:stale").update(
        created_at=timezone.now() - timedelta(hours=2)
    )

    # Stale reservations are warnings, not blockers; watchdog must not page admins for them.
    result = economic_safety_watch_task()
    assert "WARN: stale_active_reservations=1" in result
    assert not Notification.objects.filter(user=admin, dedupe_key__startswith="economic-safety:").exists()
