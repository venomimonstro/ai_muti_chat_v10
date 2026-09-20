from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.billing.services import credit

from .models import User


@pytest.mark.django_db(transaction=True)
def test_platform_admin_test_tab_uses_target_user_wallet_without_changing_admin_session():
    admin = User.objects.create_user(
        username="platform-admin-test-tab",
        email="platform-admin-test-tab@example.test",
        password="password123!",
        role=User.Role.PLATFORM_ADMIN,
    )
    target = User.objects.create_user(
        username="client-test-tab",
        email="client-test-tab@example.test",
        password="password123!",
    )
    credit(target, Decimal("37.50"), "test", "target-wallet")

    client = APIClient()
    client.force_login(admin)

    target_wallet = client.get(
        "/api/v1/wallet/",
        HTTP_X_TEST_USER=str(target.id),
    )
    assert target_wallet.status_code == 200
    assert Decimal(str(target_wallet.data["available_rub"])) == Decimal("37.5000")
    assert target_wallet.headers.get("X-Test-User-Active") == "1"
    assert target_wallet.headers.get("X-Test-User-Id") == str(target.id)

    # The same browser session remains the real platform admin when the tab-scoped
    # header is absent, so the admin console can coexist in another tab.
    me = client.get("/api/v1/auth/me/")
    assert me.status_code == 200
    assert me.data["id"] == str(admin.id)
