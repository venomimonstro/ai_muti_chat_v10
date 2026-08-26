from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.billing.services import credit, reserve, settle

from .models import Notification, SupportRequest, User, UserPreference


@pytest.mark.django_db
def test_preferences_and_support_are_user_scoped():
    user = User.objects.create_user(
        username="settings", email="settings@example.com", password="password123"
    )
    outsider = User.objects.create_user(
        username="settings-other", email="settings-other@example.com", password="password123"
    )
    SupportRequest.objects.create(user=outsider, subject="Чужое", message="Секрет")
    client = APIClient()
    client.force_authenticate(user)
    response = client.patch(
        "/api/v1/auth/preferences/",
        {"daily_spend_limit_rub": "5.00", "low_balance_threshold_rub": "2.00"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["daily_spend_limit_rub"] == "5.00"
    created = client.post(
        "/api/v1/auth/support/",
        {"subject": "Нужна помощь", "message": "Не открывается чат"},
        format="json",
    )
    assert created.status_code == 201
    listed = client.get("/api/v1/auth/support/")
    assert len(listed.data) == 1
    assert listed.data[0]["subject"] == "Нужна помощь"


@pytest.mark.django_db(transaction=True)
def test_spend_limit_counts_active_reservations_and_low_balance_notification():
    user = User.objects.create_user(
        username="limits", email="limits@example.com", password="password123"
    )
    credit(user, Decimal("10"), "test", "limits")
    UserPreference.objects.create(
        user=user,
        daily_spend_limit_rub=Decimal("1.00"),
        low_balance_threshold_rub=Decimal("10.00"),
    )
    first = reserve(user, Decimal("0.60"), "limit:first")
    with pytest.raises(ValidationError, match="дневной лимит"):
        reserve(user, Decimal("0.50"), "limit:second")
    settle(first.id, Decimal("0.20"))
    assert Notification.objects.filter(user=user, level=Notification.Level.WARNING).count() == 1


@pytest.mark.django_db
def test_change_password_keeps_current_session_authenticated():
    user = User.objects.create_user(
        username="security", email="security@example.com", password="password123"
    )
    client = APIClient()
    assert (
        client.post(
            "/api/v1/auth/login/",
            {"username": "security", "password": "password123"},
            format="json",
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/auth/change-password/",
        {"current_password": "password123", "new_password": "new-password-456"},
        format="json",
    )
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me/").status_code == 200
    user.refresh_from_db()
    assert user.check_password("new-password-456")


@pytest.mark.django_db
def test_blocked_user_loses_existing_api_session():
    user = User.objects.create_user(
        username="blocked-session", email="blocked@example.com", password="password123"
    )
    client = APIClient()
    assert client.login(username="blocked-session", password="password123")
    assert client.get("/api/v1/auth/me/").status_code == 200
    user.status = User.Status.BLOCKED
    user.save(update_fields=["status"])
    assert client.get("/api/v1/auth/me/").status_code == 403
    user.refresh_from_db()
    assert user.is_active is False
