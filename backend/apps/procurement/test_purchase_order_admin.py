from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import Provider, ProviderApiKey
from apps.procurement.models import ProviderPurchase
from apps.procurement.services import create_funding_account, record_purchase, reserve_provider_spend, settle_provider_spend


class PurchaseOrderAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="purchase-admin",
            email="purchase-admin@example.test",
            password="password123!",
            role=User.Role.PLATFORM_ADMIN,
        )
        self.provider = Provider.objects.create(
            slug="purchase-vendor",
            name="Purchase vendor",
            adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )
        self.key = ProviderApiKey(
            provider=self.provider,
            label="Ключ 1",
            enabled=True,
            priority=10,
            health_state=ProviderApiKey.HealthState.HEALTHY,
        )
        self.key.set_secret("purchase-secret")
        self.key.save()
        self.account = create_funding_account(
            provider=self.provider,
            api_key=self.key,
            label="Ключ 1",
            currency="USD",
            is_default=True,
        )
        self.purchase = record_purchase(
            account=self.account,
            credit_native=Decimal("10"),
            payment_amount=Decimal("1000"),
            payment_currency="RUB",
            fees_rub=Decimal("0"),
            purchased_at=timezone.now(),
            created_by=self.admin,
            reference="initial",
        )
        self.client = APIClient()
        self.client.force_login(self.admin)

    def _post(self, payload):
        return self.client.post("/api/v1/admin/procurement/ledger/", payload, format="json")

    def test_admin_can_edit_unconsumed_purchase_and_funding_total(self):
        response = self._post({
            "action": "edit_purchase",
            "purchase_id": str(self.purchase.id),
            "credit_native": "12",
            "payment_amount": "1500",
            "payment_currency": "RUB",
            "fees_rub": "100",
            "purchased_at": timezone.localdate().isoformat(),
            "reference": "corrected",
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.purchase.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(self.purchase.credit_native, Decimal("12.000000"))
        self.assertEqual(self.purchase.total_cash_outlay_rub, Decimal("1600.0000"))
        self.assertEqual(self.purchase.reference, "corrected")
        self.assertEqual(self.account.funded_native, Decimal("12.000000"))

    def test_admin_can_cancel_then_soft_delete_unconsumed_purchase(self):
        cancelled = self._post({"action": "cancel_purchase", "purchase_id": str(self.purchase.id)})
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.purchase.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(self.purchase.state, ProviderPurchase.State.CANCELLED)
        self.assertEqual(self.account.funded_native, Decimal("0.000000"))

        deleted = self._post({"action": "delete_purchase", "purchase_id": str(self.purchase.id)})
        self.assertEqual(deleted.status_code, 200, deleted.data)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.state, ProviderPurchase.State.DELETED)

    def test_consumed_purchase_cannot_be_rewritten_cancelled_or_deleted(self):
        reservation = reserve_provider_spend(
            provider=self.provider,
            amount_native=Decimal("1"),
            source_key="purchase-admin-spend",
        )
        settle_provider_spend(
            reservation_id=reservation.id,
            actual_native=Decimal("1"),
            nominal_cost_rub=Decimal("100"),
            customer_charge_rub=Decimal("200"),
            source_type="chat",
            source_id="purchase-admin-spend",
            model_slug="test-model",
        )
        for action in ("edit_purchase", "cancel_purchase", "delete_purchase"):
            payload = {"action": action, "purchase_id": str(self.purchase.id)}
            if action == "edit_purchase":
                payload.update({
                    "credit_native": "11",
                    "payment_amount": "1100",
                    "payment_currency": "RUB",
                    "fees_rub": "0",
                    "purchased_at": timezone.localdate().isoformat(),
                })
            response = self._post(payload)
            self.assertEqual(response.status_code, 400, (action, response.data))
