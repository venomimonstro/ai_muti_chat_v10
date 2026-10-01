import uuid

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Conversation


@pytest.mark.django_db
def test_cost_preview_hides_unexpected_internal_failure(monkeypatch):
    user = User.objects.create_user(
        username="cost-preview-failure-user",
        email="cost-preview-failure@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Failure contract")
    secret = "postgres password=TOP_SECRET provider-token=SECRET"

    def broken_preview(**_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr("apps.chat.cost_views.chat_cost_preview", broken_preview)
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/preview/",
        {
            "content": "Проверь стоимость",
            "client_message_id": str(uuid.uuid4()),
        },
        format="json",
    )

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "preflight_failed"
    assert payload["support_code"] == "preflight_internal"
    assert "Деньги не списаны" in payload["detail"]
    assert secret not in str(payload)


@pytest.mark.django_db
def test_stream_preview_hides_unexpected_internal_failure(monkeypatch):
    user = User.objects.create_user(
        username="stream-preview-failure-user",
        email="stream-preview-failure@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Stream failure contract")
    secret = "redis://:SECRET@redis:6379 internal stack"

    def broken_preview(**_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr("apps.chat.cost_views.chat_cost_preview", broken_preview)
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream/",
        {
            "content": "Ответь",
            "client_message_id": str(uuid.uuid4()),
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="safe-preflight-contract",
    )

    assert response.status_code == 503
    payload = response.json()
    assert payload["code"] == "preflight_failed"
    assert payload["support_code"] == "preflight_internal"
    assert secret not in str(payload)
