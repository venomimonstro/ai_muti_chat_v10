from decimal import Decimal

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.billing.models import LedgerEntry, Wallet

from .models import User


@pytest.mark.django_db
@override_settings(SIGNUP_PROMO_RUB="25.00")
def test_existing_authenticated_consumer_gets_signup_promo_on_me_once():
    user = User.objects.create_user(
        username="workspace-promo-existing",
        email="workspace-promo-existing@example.test",
        password="Safe-password-123!",
    )
    client = APIClient()
    client.force_authenticate(user)

    first = client.get("/api/v1/auth/me/")
    second = client.get("/api/v1/auth/me/")

    assert first.status_code == 200
    assert second.status_code == 200
    wallet = Wallet.objects.get(user=user)
    assert wallet.available_rub == Decimal("25.0000")
    assert wallet.promo_rub == Decimal("25.0000")
    assert LedgerEntry.objects.filter(
        wallet=wallet,
        idempotency_key=f"credit:signup_promo:{user.id}",
    ).count() == 1
