import uuid
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderError, ProviderStreamEvent
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import BalanceReservation, PriceVersion, RequestCost
from apps.billing.services import credit

from .models import Conversation, Generation
from .streaming import prepare, run


class PartialFailureAdapter:
    def stream(self, **_kwargs):
        yield ProviderStreamEvent(kind="delta", text_delta="частичный ответ")
        raise ProviderError("timeout", code="timeout", retryable=True)


class ChatMoneySafetyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="money-safety-user",
            email="money-safety@example.test",
            password="test-password-123",
        )
        credit(self.user, Decimal("10"), "test", "money-safety")
        provider = Provider.objects.create(
            slug="money-safety-echo",
            name="Money safety echo",
            adapter_type=Provider.AdapterType.ECHO,
            enabled=True,
            health_state=Provider.HealthState.HEALTHY,
        )
        AIModel.objects.create(
            provider=provider,
            slug="money-safety-echo-v1",
            display_name="Money safety echo",
            upstream_model="money-safety-echo-v1",
            enabled=True,
            capabilities=["text", "streaming"],
        )
        PriceVersion.objects.create(
            model_slug="money-safety-echo-v1",
            input_rub_per_million=Decimal("10"),
            output_rub_per_million=Decimal("20"),
            markup_percent=Decimal("100"),
            effective_from=timezone.now(),
        )
        self.conversation = Conversation.objects.create(
            owner=self.user,
            selected_model="money-safety-echo-v1",
            routing_mode=Conversation.RoutingMode.MANUAL,
        )

    def test_replayed_request_does_not_create_second_reservation(self):
        values = {
            "user": self.user,
            "conversation": self.conversation,
            "content": "Один запрос",
            "client_message_id": uuid.uuid4(),
            "idempotency_key": "money-safety:same",
        }
        first, first_created = prepare(**values)
        second, second_created = prepare(**values)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            BalanceReservation.objects.filter(wallet=self.user.wallet).count(),
            1,
        )
        self.assertEqual(RequestCost.objects.filter(generation_id=first.id).count(), 1)

    def test_partial_provider_failure_does_not_charge_customer_without_usage(self):
        generation, _ = prepare(
            user=self.user,
            conversation=self.conversation,
            content="Сбой после начала ответа",
            client_message_id=uuid.uuid4(),
            idempotency_key="money-safety:partial",
        )
        before = self.user.wallet.available_rub

        body = "".join(run(generation, adapter=PartialFailureAdapter()))

        generation.refresh_from_db()
        self.user.wallet.refresh_from_db()
        cost = RequestCost.objects.get(generation_id=generation.id)
        self.assertEqual(generation.state, Generation.State.FAILED)
        self.assertIn("event: error", body)
        self.assertEqual(self.user.wallet.available_rub, before)
        self.assertEqual(self.user.wallet.reserved_rub, Decimal("0.0000"))
        self.assertIsNone(cost.charged_rub)
