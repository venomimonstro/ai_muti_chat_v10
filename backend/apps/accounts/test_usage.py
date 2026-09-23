from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation, Generation, Message


def make_generation(user, cost="1.2500", input_tokens=100, output_tokens=50, model="model-a"):
    conversation = Conversation.objects.create(owner=user, title="usage")
    request = Message.objects.create(conversation=conversation, role=Message.Role.USER, content="test")
    response = Message.objects.create(conversation=conversation, role=Message.Role.ASSISTANT, content="ok")
    return Generation.objects.create(
        owner=user,
        user_message=request,
        assistant_message=response,
        state=Generation.State.COMPLETED,
        model=model,
        routed_model=model,
        idempotency_key=f"usage-{user.id}-{request.id}",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        actual_cost_rub=Decimal(cost),
        completed_at=timezone.now(),
    )


@pytest.mark.django_db
def test_usage_summary_is_user_scoped():
    alice = User.objects.create_user(username="usage-alice", email="ua@example.test", password="test-password-123")
    bob = User.objects.create_user(username="usage-bob", email="ub@example.test", password="test-password-123")
    make_generation(alice, cost="2.5000", input_tokens=200, output_tokens=80)
    make_generation(bob, cost="99.0000", input_tokens=9999, output_tokens=9999)
    client = APIClient()
    client.force_authenticate(alice)

    response = client.get("/api/v1/auth/usage/")
    assert response.status_code == 200
    assert response.data["thirty_days"]["requests"] == 1
    assert Decimal(response.data["thirty_days"]["cost_rub"]) == Decimal("2.5000")
    assert response.data["thirty_days"]["input_tokens"] == 200
    assert response.data["thirty_days"]["output_tokens"] == 80


@pytest.mark.django_db
def test_usage_hides_gigachat_internal_model_names():
    user = User.objects.create_user(username="usage-system", email="system@example.test", password="test-password-123")
    make_generation(user, cost="1.0000", model="gigachat-2-lite")
    make_generation(user, cost="2.0000", model="gigachat-2-pro")
    make_generation(user, cost="3.0000", model="gigachat-2-max")
    client = APIClient()
    client.force_authenticate(user)

    response = client.get("/api/v1/auth/usage/")
    assert response.status_code == 200
    names = {row["routed_model"] for row in response.data["by_model"]}
    assert {"System Lite", "System Pro", "System Max"}.issubset(names)
    assert all("gigachat" not in name.casefold() for name in names)
