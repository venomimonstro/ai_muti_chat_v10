from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.accounts.models import User

from .context import SYSTEM_POLICY
from .managed_stream import _publicize_sse_chunk
from .models import Conversation, Generation, Message
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
