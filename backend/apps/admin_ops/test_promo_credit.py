from decimal import Decimal

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.billing.models import LedgerEntry, Wallet


@override_settings(ADMIN_MFA_ENFORCED=False)
class AdminPromoCreditTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="promo-admin",
            email="promo-admin@example.test",
            password="test-password",
            role=User.Role.PLATFORM_ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
        )
        self.user = User.objects.create_user(
            username="promo-user",
            email="promo-user@example.test",
            password="test-password",
            status=User.Status.ACTIVE,
        )
        self.client.force_authenticate(self.admin)
        self.url = f"/api/v1/admin/users/{self.user.id}/promo-credit/"

    def test_admin_can_credit_promo_rubles(self):
        response = self.client.post(
            self.url,
            {"amount_rub": "500.00", "comment": "Тестовое начисление"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="promo-credit-test-1",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["ok"])

        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.available_rub, Decimal("500.0000"))
        self.assertEqual(wallet.promo_rub, Decimal("500.0000"))
        self.assertEqual(wallet.paid_rub, Decimal("0.0000"))
        self.assertEqual(wallet.reserved_rub, Decimal("0.0000"))

        entry = LedgerEntry.objects.get(wallet=wallet, source_type="admin_promo")
        self.assertEqual(entry.amount_rub, Decimal("500.0000"))
        self.assertEqual(entry.promo_delta_rub, Decimal("500.0000"))

    def test_admin_promo_credit_is_idempotent(self):
        headers = {"HTTP_IDEMPOTENCY_KEY": "promo-credit-test-same"}
        first = self.client.post(
            self.url,
            {"amount_rub": "250.00", "comment": "Первое начисление"},
            format="json",
            **headers,
        )
        second = self.client.post(
            self.url,
            {"amount_rub": "250.00", "comment": "Повтор запроса"},
            format="json",
            **headers,
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)

        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.available_rub, Decimal("250.0000"))
        self.assertEqual(wallet.promo_rub, Decimal("250.0000"))
        self.assertEqual(
            LedgerEntry.objects.filter(wallet=wallet, source_type="admin_promo").count(),
            1,
        )
