from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent
from .webhook_models import AgentWebhookDelivery, AgentWebhookTrigger


@pytest.mark.django_db
def test_webhook_secret_is_one_time_and_owner_scoped():
    owner = User.objects.create_user(username="wh-owner", email="wh-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="wh-other", email="wh-other@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Lead agent", objective="Process lead", status=Agent.Status.ACTIVE)
    client = APIClient()
    client.force_authenticate(owner)

    created = client.post(
        "/api/v1/agent-webhooks/",
        {"agent": str(agent.id), "name": "CRM lead", "objective": "Обработать новую заявку"},
        format="json",
    )
    assert created.status_code == 201
    assert created.data["secret"]
    trigger_id = created.data["id"]

    listed = client.get("/api/v1/agent-webhooks/")
    assert listed.status_code == 200
    assert len(listed.data) == 1
    assert "secret" not in listed.data[0]

    client.force_authenticate(other)
    hidden = client.get(f"/api/v1/agent-webhooks/{trigger_id}/")
    assert hidden.status_code == 404


@pytest.mark.django_db
def test_webhook_requires_secret_and_event_id_and_is_idempotent():
    owner = User.objects.create_user(username="wh-invoke", email="wh-invoke@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Lead agent", objective="Process lead", status=Agent.Status.ACTIVE)
    client = APIClient()
    client.force_authenticate(owner)
    created = client.post(
        "/api/v1/agent-webhooks/",
        {"agent": str(agent.id), "name": "CRM lead"},
        format="json",
    )
    trigger_id = created.data["id"]
    secret = created.data["secret"]
    client.force_authenticate(user=None)
    url = f"/api/v1/agent-webhooks/{trigger_id}/invoke/"

    unauthorized = client.post(url, {"phone": "+70000000000"}, format="json", HTTP_IDEMPOTENCY_KEY="lead-1")
    assert unauthorized.status_code == 401

    missing_event = client.post(url, {"phone": "+70000000000"}, format="json", HTTP_X_AGENT_WEBHOOK_SECRET=secret)
    assert missing_event.status_code == 400

    with patch("apps.agents.webhook_views.dispatch_agent_webhook_delivery.delay") as delay:
        first = client.post(
            url,
            {"phone": "+70000000000", "source": "site"},
            format="json",
            HTTP_X_AGENT_WEBHOOK_SECRET=secret,
            HTTP_IDEMPOTENCY_KEY="lead-1",
        )
        second = client.post(
            url,
            {"phone": "+70000000000", "source": "site"},
            format="json",
            HTTP_X_AGENT_WEBHOOK_SECRET=secret,
            HTTP_IDEMPOTENCY_KEY="lead-1",
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.data["delivery_id"] == second.data["delivery_id"]
    assert first.data["duplicate"] is False
    assert second.data["duplicate"] is True
    assert AgentWebhookDelivery.objects.filter(trigger_id=trigger_id, event_id="lead-1").count() == 1
    delay.assert_called_once()


@pytest.mark.django_db
def test_rotating_webhook_secret_invalidates_old_secret():
    owner = User.objects.create_user(username="wh-rotate", email="wh-rotate@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Agent", objective="Process", status=Agent.Status.ACTIVE)
    client = APIClient()
    client.force_authenticate(owner)
    created = client.post("/api/v1/agent-webhooks/", {"agent": str(agent.id), "name": "Hook"}, format="json")
    old_secret = created.data["secret"]
    trigger_id = created.data["id"]

    rotated = client.post(f"/api/v1/agent-webhooks/{trigger_id}/rotate-secret/", {}, format="json")
    assert rotated.status_code == 200
    new_secret = rotated.data["secret"]
    assert new_secret != old_secret

    client.force_authenticate(user=None)
    url = f"/api/v1/agent-webhooks/{trigger_id}/invoke/"
    old = client.post(url, {"value": 1}, format="json", HTTP_X_AGENT_WEBHOOK_SECRET=old_secret, HTTP_X_EVENT_ID="evt-old")
    assert old.status_code == 401

    with patch("apps.agents.webhook_views.dispatch_agent_webhook_delivery.delay"):
        new = client.post(url, {"value": 1}, format="json", HTTP_X_AGENT_WEBHOOK_SECRET=new_secret, HTTP_X_EVENT_ID="evt-new")
    assert new.status_code == 202


@pytest.mark.django_db
def test_foreign_subject_cannot_be_bound_to_webhook():
    owner = User.objects.create_user(username="wh-a", email="wh-a@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="wh-b", email="wh-b@example.com", password="StrongPass123!")
    foreign_agent = Agent.objects.create(owner=other, name="Private agent", objective="Private", status=Agent.Status.ACTIVE)
    client = APIClient()
    client.force_authenticate(owner)
    response = client.post("/api/v1/agent-webhooks/", {"agent": str(foreign_agent.id), "name": "Bad"}, format="json")
    assert response.status_code == 400
    assert AgentWebhookTrigger.objects.filter(owner=owner).count() == 0
