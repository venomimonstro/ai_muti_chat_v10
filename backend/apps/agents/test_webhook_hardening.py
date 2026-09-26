from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent
from .webhook_models import AgentWebhookDelivery, AgentWebhookTrigger
from .webhook_tasks import dispatch_agent_webhook_delivery


@pytest.mark.django_db
def test_disabled_webhook_cannot_be_invoked():
    owner = User.objects.create_user(username="wh-disabled", email="wh-disabled@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Agent", objective="Process event", status=Agent.Status.ACTIVE)
    client = APIClient()
    client.force_authenticate(owner)
    created = client.post("/api/v1/agent-webhooks/", {"agent": str(agent.id), "name": "Hook"}, format="json")
    trigger_id = created.data["id"]
    secret = created.data["secret"]
    AgentWebhookTrigger.objects.filter(pk=trigger_id).update(enabled=False)

    client.force_authenticate(user=None)
    response = client.post(
        f"/api/v1/agent-webhooks/{trigger_id}/invoke/",
        {"event": "lead"},
        format="json",
        HTTP_X_AGENT_WEBHOOK_SECRET=secret,
        HTTP_IDEMPOTENCY_KEY="evt-disabled",
    )

    assert response.status_code == 404
    assert not AgentWebhookDelivery.objects.filter(trigger_id=trigger_id).exists()


@pytest.mark.django_db
def test_pending_delivery_for_inactive_agent_fails_without_run_or_charge():
    owner = User.objects.create_user(username="wh-paused", email="wh-paused@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Paused agent", objective="Process event", status=Agent.Status.PAUSED)
    trigger = AgentWebhookTrigger.objects.create(
        owner=owner,
        agent=agent,
        name="Hook",
        objective="Process event",
        secret_hash="not-empty-for-worker-test",
    )
    delivery = AgentWebhookDelivery.objects.create(trigger=trigger, event_id="evt-paused", payload={"lead": 1})

    result = dispatch_agent_webhook_delivery.run(str(delivery.id))
    delivery.refresh_from_db()

    assert result["state"] == AgentWebhookDelivery.State.FAILED
    assert delivery.state == AgentWebhookDelivery.State.FAILED
    assert delivery.run_id is None
    assert "приостановлен" in delivery.error_message.lower()


@pytest.mark.django_db
def test_webhook_worker_is_idempotent_after_delivery_has_run():
    owner = User.objects.create_user(username="wh-worker-idem", email="wh-worker-idem@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Agent", objective="Process event", status=Agent.Status.ACTIVE)
    trigger = AgentWebhookTrigger.objects.create(
        owner=owner,
        agent=agent,
        name="Hook",
        objective="Process event",
        secret_hash="not-empty-for-worker-test",
    )
    from .models import AgentRun

    run = AgentRun.objects.create(
        owner=owner,
        agent=agent,
        objective="Existing webhook run",
        state=AgentRun.State.QUEUED,
        input_payload={"event_id": "evt-once", "webhook_trigger_id": str(trigger.id)},
    )
    delivery = AgentWebhookDelivery.objects.create(
        trigger=trigger,
        event_id="evt-once",
        payload={"lead": 1},
        state=AgentWebhookDelivery.State.QUEUED,
        run=run,
    )

    first = dispatch_agent_webhook_delivery.run(str(delivery.id))
    second = dispatch_agent_webhook_delivery.run(str(delivery.id))

    assert first["run_id"] == str(run.id)
    assert second["run_id"] == str(run.id)
    assert AgentRun.objects.filter(pk=run.id).count() == 1
    assert AgentWebhookDelivery.objects.filter(trigger=trigger, event_id="evt-once").count() == 1


@pytest.mark.django_db
def test_webhook_audit_rejects_cross_tenant_trigger():
    owner = User.objects.create_user(username="wh-audit-a", email="wh-audit-a@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="wh-audit-b", email="wh-audit-b@example.com", password="StrongPass123!")
    foreign_agent = Agent.objects.create(owner=other, name="Foreign", objective="Private", status=Agent.Status.ACTIVE)
    AgentWebhookTrigger.objects.create(
        owner=owner,
        agent=foreign_agent,
        name="Corrupt hook",
        objective="Process",
        secret_hash="hash",
    )

    with pytest.raises(CommandError, match="Agent webhook audit failed"):
        call_command("agent_webhook_audit")


@pytest.mark.django_db
def test_webhook_audit_rejects_stale_pending_delivery():
    owner = User.objects.create_user(username="wh-stale", email="wh-stale@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Agent", objective="Process", status=Agent.Status.ACTIVE)
    trigger = AgentWebhookTrigger.objects.create(
        owner=owner,
        agent=agent,
        name="Hook",
        objective="Process",
        secret_hash="hash",
    )
    delivery = AgentWebhookDelivery.objects.create(trigger=trigger, event_id="evt-stale", payload={})
    AgentWebhookDelivery.objects.filter(pk=delivery.pk).update(updated_at=timezone.now() - timedelta(minutes=20))

    with pytest.raises(CommandError, match="Agent webhook audit failed"):
        call_command("agent_webhook_audit")


@pytest.mark.django_db
def test_webhook_audit_accepts_clean_empty_state():
    call_command("agent_webhook_audit")
