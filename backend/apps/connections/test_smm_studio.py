from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agents.models import Agent, AgentRun

from .models import AgentConnectionBinding, ExternalConnection
from .smm_media import _stock_image_bytes
from .smm_models import SMMContentItem, SMMContentPlan, SMMPublicationAttempt
from .smm_service import due_items, ensure_smm_agent, publish_item, sync_generated_plan


def _user(name="smm-owner"):
    return User.objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password="StrongPass123!",
    )


def _vk_connection(user, *, selected=True):
    connection = ExternalConnection(
        owner=user,
        kind=ExternalConnection.Kind.VK,
        name="VK Business",
        base_url="https://api.vk.com/method",
        username="101",
        enabled=True,
        health_state=ExternalConnection.Health.HEALTHY,
        metadata={
            "groups": [{"id": "777", "name": "Business", "screen_name": "business"}],
            **({"selected_group_id": "777", "selected_group_name": "Business"} if selected else {}),
        },
    )
    connection.set_secret("test-vk-token")
    connection.save()
    return connection


def _agent(user):
    return Agent.objects.create(
        owner=user,
        name="SMM-специалист VK",
        role="SMM-специалист ВКонтакте",
        objective="Готовить контент VK",
        autonomy=Agent.Autonomy.SEMI_AUTONOMOUS,
        status=Agent.Status.ACTIVE,
    )


def _plan(user, connection, agent, *, auto_publish=False):
    return SMMContentPlan.objects.create(
        owner=user,
        agent=agent,
        connection=connection,
        title="Контент-план",
        period_start=timezone.localdate(),
        period_end=timezone.localdate() + timedelta(days=30),
        status=SMMContentPlan.Status.ACTIVE,
        auto_publish=auto_publish,
    )


@pytest.mark.django_db
def test_smm_bootstrap_requires_selected_vk_group():
    user = _user()
    connection = _vk_connection(user, selected=False)
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/smm/plans/bootstrap-agent/",
        {"connection": str(connection.id)},
        format="json",
    )

    assert response.status_code == 400
    assert Agent.objects.filter(owner=user, role="SMM-специалист ВКонтакте").count() == 0


@pytest.mark.django_db
def test_smm_plan_and_items_are_tenant_scoped():
    owner = _user("smm-owner-a")
    stranger = _user("smm-owner-b")
    connection = _vk_connection(owner)
    plan = _plan(owner, connection, _agent(owner))
    item = SMMContentItem.objects.create(plan=plan, title="Private", content="Secret")
    client = APIClient()
    client.force_authenticate(stranger)

    plan_response = client.get(f"/api/v1/smm/plans/{plan.id}/")
    item_response = client.patch(
        f"/api/v1/smm/items/{item.id}/",
        {"content": "hijacked"},
        format="json",
    )

    assert plan_response.status_code == 404
    assert item_response.status_code == 404
    item.refresh_from_db()
    assert item.content == "Secret"


@pytest.mark.django_db
def test_scheduling_post_enables_plan_autopublish():
    user = _user()
    connection = _vk_connection(user)
    plan = _plan(user, connection, _agent(user))
    item = SMMContentItem.objects.create(
        plan=plan,
        title="Запланированный пост",
        content="Полезный пост для аудитории",
        status=SMMContentItem.Status.APPROVED,
    )
    client = APIClient()
    client.force_authenticate(user)
    scheduled_at = timezone.now() + timedelta(hours=2)

    response = client.post(
        f"/api/v1/smm/items/{item.id}/schedule/",
        {"scheduled_at": scheduled_at.isoformat()},
        format="json",
    )

    assert response.status_code == 200
    item.refresh_from_db()
    plan.refresh_from_db()
    assert item.status == SMMContentItem.Status.SCHEDULED
    assert item.scheduled_at is not None
    assert plan.auto_publish is True


@pytest.mark.django_db
def test_smm_agent_bootstrap_reuses_paused_agent_and_binding():
    user = _user("smm-reuse")
    connection = _vk_connection(user)
    agent = Agent.objects.create(
        owner=user,
        name="SMM-специалист VK",
        role="Old role",
        objective="Old objective",
        status=Agent.Status.PAUSED,
    )
    binding = AgentConnectionBinding.objects.create(
        agent=agent,
        connection=connection,
        purpose="publish",
        enabled=False,
    )

    resolved = ensure_smm_agent(owner=user, connection=connection)

    assert resolved.id == agent.id
    resolved.refresh_from_db()
    binding.refresh_from_db()
    assert resolved.status == Agent.Status.ACTIVE
    assert resolved.tool_policy["web"] is True
    assert resolved.tool_policy["publish"] is False
    assert binding.enabled is True
    assert Agent.objects.filter(owner=user, name="SMM-специалист VK").count() == 1


@pytest.mark.django_db
def test_generated_plan_sync_is_idempotent_and_clamps_bad_dates():
    user = _user("smm-sync")
    connection = _vk_connection(user)
    agent = _agent(user)
    plan = _plan(user, connection, agent)
    valid_day = timezone.localdate() + timedelta(days=1)
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Generate plan",
        state=AgentRun.State.COMPLETED,
        output_payload={
            "text": (
                '[{"title":"Post 1","topic":"One","objective":"Trust",'
                '"content":"Text one","cta":"Write us","hashtags":["#one"],'
                f'"scheduled_at":"{valid_day.isoformat()}T12:00:00+03:00","media_prompt":"Image one"}},'
                '{"title":"Post 2","topic":"Two","objective":"Lead",'
                '"content":"Text two","cta":"","hashtags":[],"scheduled_at":"2099-01-01T12:00:00+03:00",'
                '"media_prompt":"Image two"}]'
            )
        },
    )
    plan.generation_run = run
    plan.save(update_fields=["generation_run", "updated_at"])

    first = sync_generated_plan(plan)
    second = sync_generated_plan(plan)

    assert first["created"] == 2
    assert second["created"] == 0
    assert SMMContentItem.objects.filter(plan=plan).count() == 2
    assert SMMContentItem.objects.filter(plan=plan, scheduled_at__isnull=False).count() == 1


@pytest.mark.django_db
def test_vk_publish_is_idempotent(monkeypatch):
    user = _user()
    connection = _vk_connection(user)
    plan = _plan(user, connection, _agent(user))
    item = SMMContentItem.objects.create(
        plan=plan,
        title="Пост",
        content="Текст поста",
        status=SMMContentItem.Status.APPROVED,
    )
    calls = []

    def fake_publish(*args, **kwargs):
        calls.append(kwargs)
        return "-777_55"

    monkeypatch.setattr("apps.connections.smm_service.publish_wall_post", fake_publish)

    first = publish_item(item, idempotency_key="same-key")
    second = publish_item(item, idempotency_key="same-key")

    assert first.id == second.id
    assert first.state == SMMPublicationAttempt.State.COMPLETED
    assert len(calls) == 1
    item.refresh_from_db()
    assert item.external_post_id == "-777_55"
    assert item.status == SMMContentItem.Status.PUBLISHED


@pytest.mark.django_db
def test_failed_publish_retries_same_key_with_same_vk_guid(monkeypatch):
    user = _user("smm-retry")
    connection = _vk_connection(user)
    plan = _plan(user, connection, _agent(user))
    item = SMMContentItem.objects.create(
        plan=plan,
        title="Retry",
        content="Retry body",
        status=SMMContentItem.Status.APPROVED,
    )
    guids = []

    def fake_publish(*args, **kwargs):
        guids.append(kwargs["request_guid"])
        if len(guids) == 1:
            raise ValidationError("temporary upstream failure")
        return "-777_56"

    monkeypatch.setattr("apps.connections.smm_service.publish_wall_post", fake_publish)

    with pytest.raises(ValidationError):
        publish_item(item, idempotency_key="retry-key")
    item.refresh_from_db()
    assert item.status == SMMContentItem.Status.FAILED

    attempt = publish_item(item, idempotency_key="retry-key")
    item.refresh_from_db()

    assert guids[0] == guids[1]
    assert attempt.state == SMMPublicationAttempt.State.COMPLETED
    assert item.external_post_id == "-777_56"
    assert SMMPublicationAttempt.objects.filter(item=item).count() == 1


@pytest.mark.django_db
def test_due_posts_require_auto_publish_and_healthy_connection():
    user = _user("smm-due")
    connection = _vk_connection(user)
    agent = _agent(user)
    enabled_plan = _plan(user, connection, agent, auto_publish=True)
    disabled_plan = _plan(user, connection, agent, auto_publish=False)
    due_at = timezone.now() - timedelta(minutes=1)
    enabled_item = SMMContentItem.objects.create(
        plan=enabled_plan,
        title="Due",
        content="Publish me",
        status=SMMContentItem.Status.SCHEDULED,
        scheduled_at=due_at,
    )
    SMMContentItem.objects.create(
        plan=disabled_plan,
        title="Manual",
        content="Do not publish",
        status=SMMContentItem.Status.SCHEDULED,
        scheduled_at=due_at,
    )

    ids = {item.id for item in due_items(limit=20)}
    assert ids == {enabled_item.id}

    connection.health_state = ExternalConnection.Health.DEGRADED
    connection.save(update_fields=["health_state", "updated_at"])
    assert due_items(limit=20) == []


@pytest.mark.django_db
def test_stock_image_download_rejects_untrusted_host():
    with pytest.raises(ValidationError):
        _stock_image_bytes("https://example.com/untrusted.jpg")
