from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion

from .models import Conversation


def _ready_fallback_model():
    provider = Provider.objects.create(
        slug="manual-race-echo",
        name="Manual race Echo",
        adapter_type=Provider.AdapterType.ECHO,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    model = AIModel.objects.create(
        provider=provider,
        slug="manual-race-fallback",
        display_name="Manual race fallback",
        upstream_model="manual-race-fallback",
        enabled=True,
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=2048,
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("1"),
        output_rub_per_million=Decimal("2"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    return model


@pytest.mark.django_db
def test_new_manual_chat_recovers_when_selected_model_disappears_after_picker_refresh():
    user = User.objects.create_user(
        username="manual-create-race",
        email="manual-create-race@example.test",
        password="password123!",
    )
    fallback = _ready_fallback_model()
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/conversations/",
        {
            "title": "Новый чат",
            "routing_mode": "manual",
            "selected_model": "model-that-disappeared-after-picker-refresh",
        },
        format="json",
    )

    assert response.status_code == 201
    conversation = Conversation.objects.get(pk=response.data["id"])
    assert conversation.routing_mode == Conversation.RoutingMode.MANUAL
    assert conversation.selected_model == fallback.slug


@pytest.mark.django_db
def test_new_manual_chat_fails_cleanly_when_selected_model_disappears_and_no_fallback_exists():
    user = User.objects.create_user(
        username="manual-create-no-fallback",
        email="manual-create-no-fallback@example.test",
        password="password123!",
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/conversations/",
        {
            "title": "Новый чат",
            "routing_mode": "manual",
            "selected_model": "model-that-disappeared-after-picker-refresh",
        },
        format="json",
    )

    assert response.status_code == 400
    assert Conversation.objects.filter(owner=user).count() == 0


@pytest.mark.django_db
def test_existing_chat_cannot_be_explicitly_switched_to_dead_manual_model():
    user = User.objects.create_user(
        username="manual-patch-race",
        email="manual-patch-race@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(
        owner=user,
        title="Existing",
        routing_mode=Conversation.RoutingMode.AUTO,
        selected_model="echo-v1",
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/conversations/{conversation.id}/",
        {"routing_mode": "manual", "selected_model": "dead-manual-model"},
        format="json",
    )

    assert response.status_code == 400
    conversation.refresh_from_db()
    assert conversation.routing_mode == Conversation.RoutingMode.AUTO
    assert conversation.selected_model == "echo-v1"
