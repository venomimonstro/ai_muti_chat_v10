from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion, RequestCost
from apps.chat.models import Conversation, Generation, Message

from .models import ProviderFundingAccount, ProviderSpendReservation
from .services import reserve_provider_spend


class ChatProcurementCleanupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="cleanup@example.com", password="test-pass-123")
        self.provider = Provider.objects.create(
            slug="cleanup-provider",
            name="Cleanup provider",
            adapter_type=Provider.AdapterType.ECHO,
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )
        self.model = AIModel.objects.create(
            provider=self.provider,
            slug="cleanup-model",
            display_name="Cleanup model",
            upstream_model="echo-v1",
        )
        self.conversation = Conversation.objects.create(owner=self.user, title="Cleanup")
        self.user_message = Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.USER,
            content="test",
        )
        self.assistant_message = Message.objects.create(
            conversation=self.conversation,
            role=Message.Role.ASSISTANT,
            content="",
        )
        self.generation = Generation.objects.create(
            owner=self.user,
            user_message=self.user_message,
            assistant_message=self.assistant_message,
            model=self.model.slug,
            state=Generation.State.RUNNING,
            idempotency_key="cleanup-generation",
        )
        self.price = PriceVersion.objects.create(
            model_slug=self.model.slug,
            provider_currency="RUB",
            input_rub_per_million=Decimal("1"),
            output_rub_per_million=Decimal("1"),
            markup_percent=Decimal("100"),
        )
        self.request_cost = RequestCost.objects.create(
            generation_id=self.generation.id,
            price_version=self.price,
            estimated_rub=Decimal("1"),
            expected_provider_cost_rub=Decimal("0.5"),
            pricing_snapshot={"fx_rate": "1"},
        )

    def _active_reservation(self):
        # Echo does not require procurement through normal signals, so create a
        # funding account/reservation explicitly to exercise terminal cleanup.
        ProviderFundingAccount.objects.create(
            provider=self.provider,
            label="cleanup funding",
            credential_env="CLEANUP_TEST_API_KEY",
            currency="RUB",
            active=True,
            is_default=True,
            funded_native=Decimal("10"),
        )
        reservation = ProviderSpendReservation.objects.create(
            account=self.provider.funding_accounts.get(is_default=True),
            amount_native=Decimal("1"),
            source_key=f"chat:{self.request_cost.id}:{self.price.id}",
        )
        account = reservation.account
        account.reserved_native = Decimal("1")
        account.save(update_fields=["reserved_native", "updated_at"])
        return reservation

    def test_failed_generation_releases_unconfirmed_provider_reservation(self):
        reservation = self._active_reservation()
        self.generation.state = Generation.State.FAILED
        self.generation.save(update_fields=["state"])
        reservation.refresh_from_db()
        self.assertEqual(reservation.state, ProviderSpendReservation.State.RELEASED)

    def test_confirmed_provider_cost_is_not_released_on_late_failure(self):
        reservation = self._active_reservation()
        RequestCost.objects.filter(pk=self.request_cost.pk).update(provider_cost_rub=Decimal("0.5"))
        self.generation.state = Generation.State.FAILED
        self.generation.save(update_fields=["state"])
        reservation.refresh_from_db()
        self.assertEqual(reservation.state, ProviderSpendReservation.State.ACTIVE)
