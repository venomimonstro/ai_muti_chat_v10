from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.billing.services import credit

from .models import User


class TabScopedImpersonationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="platform-admin-test-tab",
            email="platform-admin-test-tab@example.test",
            password="password123!",
            role=User.Role.PLATFORM_ADMIN,
        )
        self.target = User.objects.create_user(
            username="client-test-tab",
            email="client-test-tab@example.test",
            password="password123!",
        )
        credit(self.target, Decimal("37.50"), "test", "target-wallet")
        self.client = APIClient()
        self.client.force_login(self.admin)

    def test_platform_admin_test_tab_uses_target_user_wallet_without_changing_admin_session(self):
        target_wallet = self.client.get(
            "/api/v1/wallet/",
            HTTP_X_TEST_USER=str(self.target.id),
        )
        self.assertEqual(target_wallet.status_code, 200)
        self.assertEqual(
            Decimal(str(target_wallet.data["available_rub"])),
            Decimal("37.5000"),
        )
        self.assertEqual(target_wallet.headers.get("X-Test-User-Active"), "1")
        self.assertEqual(target_wallet.headers.get("X-Test-User-Id"), str(self.target.id))

        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["id"], str(self.admin.id))

    def test_normal_user_login_in_second_tab_preserves_real_admin_session(self):
        original_session_key = self.client.session.session_key
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": self.target.username, "password": "password123!"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["test_user_mode"])
        self.assertEqual(response.data["test_user_id"], str(self.target.id))
        self.assertEqual(response.headers.get("X-Test-User-Active"), "1")
        self.assertEqual(self.client.session.session_key, original_session_key)

        admin_me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(admin_me.status_code, 200)
        self.assertEqual(admin_me.data["id"], str(self.admin.id))

        target_me = self.client.get(
            "/api/v1/auth/me/",
            HTTP_X_TEST_USER=str(self.target.id),
        )
        self.assertEqual(target_me.status_code, 200)
        self.assertEqual(target_me.data["id"], str(self.target.id))

    def test_logout_in_test_user_tab_never_logs_out_real_admin(self):
        response = self.client.post(
            "/api/v1/auth/logout/",
            HTTP_X_TEST_USER=str(self.target.id),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["test_user_mode_ended"])
        self.assertEqual(response.headers.get("X-Test-User-Active"), "0")

        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["id"], str(self.admin.id))

    def test_logout_all_in_test_user_tab_never_logs_out_real_admin(self):
        response = self.client.post(
            "/api/v1/auth/logout-all/",
            HTTP_X_TEST_USER=str(self.target.id),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["test_user_mode_ended"])

        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["id"], str(self.admin.id))
