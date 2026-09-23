import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import BalanceReservation, Wallet

from .context import SYSTEM_POLICY
from .managed_stream import _publicize_sse_chunk
from .models import Conversation, Generation, Message
from .product_identity import (
    create_identity_generation,
    direct_identity_answer,
    identity_sse,
)
from .serializers import MessageSerializer


@pytest.mark.django_db
def test_gigachat_is_publicly_serialized_as_system_tier_without_internal_prompt():
    user = User.objects.create_user(username="public-system", password="password123")
    conversation = Conversation.objects.create(
        owner=user,
        routing_mode=Conversation.RoutingMode.BALANCED,
        selected_model="echo-v1",
    )
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Привет",
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="Ответ",
        status=Message.Status.COMPLETED,
    )
    Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.COMPLETED,
        model="gigachat-2-pro",
        routed_model="gigachat-2-pro",
        provider_slug="gigachat",
        idempotency_key="public-system-test",
        actual_cost_rub=Decimal("0.0100"),
        completed_at=timezone.now(),
        context_snapshot={
            "routing": {
                "mode": "balanced",
                "selected_model": "gigachat-2-pro",
                "model_version": "GigaChat-2-Pro",
                "exact_api_id": "GigaChat-2-Pro",
                "candidates": [{"provider": "gigachat", "model": "gigachat-2-pro"}],
            },
            "components": [
                {"kind": "system_policy", "content": "internal GigaChat policy"},
                {"kind": "product_identity", "content": "internal product identity"},
                {"kind": "recent_message", "content": "Привет"},
            ],
        },
    )

    payload = MessageSerializer(assistant).data["generation"]
    assert payload["model"] == "System Pro"
    assert payload["provider"] == "system"
    assert payload["model_version"] == "System Pro"
    assert payload["exact_api_id"] == ""
    assert payload["context"]["routing"]["selected_model"] == "System Pro"
    assert payload["context"]["routing"]["candidates"] == []
    assert [item["kind"] for item in payload["context"]["components"]] == ["recent_message"]
    assert "gigachat" not in str(payload).casefold()


def test_customer_sse_rewrites_internal_gigachat_names():
    generation = SimpleNamespace(
        user_message=SimpleNamespace(
            conversation=SimpleNamespace(routing_mode=Conversation.RoutingMode.MAXIMUM)
        )
    )
    chunk = (
        'event: completed\n'
        'data: {"state":"completed","model":"gigachat-2-max","model_version":"GigaChat-2-Max","provider":"gigachat"}\n\n'
    )
    public = _publicize_sse_chunk(generation, chunk)
    assert '"model": "System Max"' in public
    assert '"model_version": "System Max"' in public
    assert '"provider": "system"' in public
    assert "gigachat" not in public.casefold()


def test_system_identity_policy_is_stable_and_bbtc_owned():
    assert "Я ваш агент." in SYSTEM_POLICY
    assert "Компания BBTEC." in SYSTEM_POLICY
    assert "System Lite" in SYSTEM_POLICY
    assert "System Pro" in SYSTEM_POLICY
    assert "System Max" in SYSTEM_POLICY


def test_direct_identity_classifier_only_captures_short_identity_questions():
    assert direct_identity_answer("Кто ты?") == "Я ваш агент."
    assert direct_identity_answer("ТЫ КТО?!") == "Я ваш агент."
    assert direct_identity_answer("Кто тебя создал?") == "Компания BBTEC."
    assert direct_identity_answer("кто твой разработчик") == "Компания BBTEC."
    assert direct_identity_answer("Кто ты и напиши мне SEO-стратегию") is None
    assert direct_identity_answer("Кто ты?", [uuid.uuid4()]) is None


@pytest.mark.django_db(transaction=True)
def test_identity_generation_is_free_and_idempotent():
    user = User.objects.create_user(username="identity-free", email="identity-free@example.test")
    conversation = Conversation.objects.create(
        owner=user,
        routing_mode=Conversation.RoutingMode.BALANCED,
        selected_model="echo-v1",
    )
    client_message_id = uuid.uuid4()
    first, created = create_identity_generation(
        user=user,
        conversation=conversation,
        content="Кто ты?",
        client_message_id=client_message_id,
        idempotency_key="identity-free-key",
        answer="Я ваш агент.",
    )
    second, replay_created = create_identity_generation(
        user=user,
        conversation=conversation,
        content="Кто ты?",
        client_message_id=client_message_id,
        idempotency_key="identity-free-key",
        answer="Я ваш агент.",
    )

    assert created is True
    assert replay_created is False
    assert second.id == first.id
    assert first.state == Generation.State.COMPLETED
    assert first.actual_cost_rub == Decimal("0.0000")
    assert first.reservation_id is None
    assert first.provider_slug == "system"
    assert first.routed_model == "System Pro"
    assert Message.objects.filter(conversation=conversation).count() == 2
    assert BalanceReservation.objects.count() == 0
    assert Wallet.objects.filter(user=user).count() == 0
    stream = "".join(identity_sse(first))
    assert "Я ваш агент." in stream
    assert '"cost_rub": "0.0000"' in stream
    assert "gigachat" not in stream.casefold()


@pytest.mark.django_db
def test_identity_preview_is_zero_cost_even_without_provider_models():
    user = User.objects.create_user(username="identity-preview", email="identity-preview@example.test")
    conversation = Conversation.objects.create(
        owner=user,
        routing_mode=Conversation.RoutingMode.MAXIMUM,
        selected_model="echo-v1",
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/preview/",
        {"content": "Кто тебя создал?", "client_message_id": str(uuid.uuid4()), "file_ids": []},
        format="json",
    )

    assert response.status_code == 200
    assert Decimal(response.data["estimated_max_rub"]) == Decimal("0")
    assert response.data["confirmation_required"] is False
    assert response.data["selected_model"] == "System Max"
    assert Wallet.objects.filter(user=user).count() == 0


@pytest.mark.django_db(transaction=True)
def test_rest_identity_message_is_deterministic_and_free_without_provider_model():
    user = User.objects.create_user(username="identity-rest", email="identity-rest@example.test")
    conversation = Conversation.objects.create(
        owner=user,
        routing_mode=Conversation.RoutingMode.ECONOMY,
        selected_model="echo-v1",
    )
    client = APIClient()
    client.force_authenticate(user)
    message_id = uuid.uuid4()

    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/",
        {"content": "Кто тебя создал?", "client_message_id": str(message_id), "file_ids": []},
        format="json",
        HTTP_IDEMPOTENCY_KEY="identity-rest-key",
    )

    assert response.status_code == 200
    assert response.data["state"] == Generation.State.COMPLETED
    assert response.data["message"]["content"] == "Компания BBTEC."
    assert response.data["message"]["generation"]["model"] == "System Lite"
    assert response.data["message"]["generation"]["provider"] == "system"
    assert Decimal(response.data["message"]["generation"]["cost_rub"]) == Decimal("0")
    assert Wallet.objects.filter(user=user).count() == 0
    assert BalanceReservation.objects.filter(wallet__user=user).count() == 0
