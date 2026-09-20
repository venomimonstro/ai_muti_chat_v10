from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.billing.services import credit

from .models import User


class TabScopedImpersonationTests(TestCase):
    def test_platform_admin_test_tab_uses_target_user_wallet_without_changing_admin_session(self):
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
        self.assertEqual(target_wallet.status_code, 200)
        self.assertEqual(
            Decimal(str(target_wallet.data["available_rub"])),
            Decimal("37.5000"),
        )
        self.assertEqual(target_wallet.headers.get("X-Test-User-Active"), "1")
        self.assertEqual(target_wallet.headers.get("X-Test-User-Id"), str(target.id))

        me = client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["id"], str(admin.id))
