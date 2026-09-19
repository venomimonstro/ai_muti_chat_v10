from decimal import Decimal

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.billing.models import LedgerEntry, Wallet
from apps.billing.services import credit


@override_settings(ADMIN_MFA_ENFORCED=False)
class AdminBalanceDebitTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="debit-admin",
            email="debit-admin@example.test",
            password="test-password",
            role=User.Role.PLATFORM_ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
        )
        self.user = User.objects.create_user(
            username="debit-user",
            email="debit-user@example.test",
            password="test-password",
            status=User.Status.ACTIVE,
        )
        credit(self.user, Decimal("200.00"), "test_paid", "paid-1", bucket="paid")
        credit(self.user, Decimal("100.00"), "test_promo", "promo-1", bucket="promo")
        self.client.force_authenticate(self.admin)
        self.url = f"/api/v1/admin/users/{self.user.id}/action/"

    def test_admin_debit_consumes_promo_then_paid_and_writes_ledger(self):
        response = self.client.post(
            self.url,
            {
                "action": "balance_debit",
                "amount_rub": "150.00",
                "comment": "Тестовое ручное списание",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="manual-debit-test-1",
        )
        self.assertEqual(response.status_code, 200, response.data)

        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.available_rub, Decimal("150.0000"))
        self.assertEqual(wallet.promo_rub, Decimal("0.0000"))
        self.assertEqual(wallet.paid_rub, Decimal("150.0000"))
        self.assertEqual(wallet.reserved_rub, Decimal("0.0000"))

        entry = LedgerEntry.objects.filter(
            wallet=wallet,
            kind=LedgerEntry.Kind.ADJUSTMENT,
        ).order_by("-created_at").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.amount_rub, Decimal("150.0000"))
        self.assertEqual(entry.available_delta_rub, Decimal("-150.0000"))
        self.assertEqual(entry.promo_delta_rub, Decimal("-100.0000"))
        self.assertEqual(entry.paid_delta_rub, Decimal("-50.0000"))

    def test_admin_debit_rejects_amount_above_available_balance(self):
        response = self.client.post(
            self.url,
            {
                "action": "balance_debit",
                "amount_rub": "301.00",
                "comment": "Проверка лимита",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="manual-debit-test-2",
        )
        self.assertEqual(response.status_code, 400)
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual(wallet.available_rub, Decimal("300.0000"))
