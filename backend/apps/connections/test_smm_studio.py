from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agents.models import Agent

from .models import ExternalConnection
from .smm_media import _stock_image_bytes
from .smm_models import SMMContentItem, SMMContentPlan, SMMPublicationAttempt
from .smm_service import publish_item


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


def _plan(user, connection, agent):
    return SMMContentPlan.objects.create(
        owner=user,
        agent=agent,
        connection=connection,
        title="Контент-план",
        period_start=timezone.localdate(),
        period_end=timezone.localdate() + timedelta(days=30),
        status=SMMContentPlan.Status.ACTIVE,
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
def test_smm_plan_is_tenant_scoped():
    owner = _user("smm-owner-a")
    stranger = _user("smm-owner-b")
    connection = _vk_connection(owner)
    plan = _plan(owner, connection, _agent(owner))
    client = APIClient()
    client.force_authenticate(stranger)

    response = client.get(f"/api/v1/smm/plans/{plan.id}/")

    assert response.status_code == 404


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
def test_stock_image_download_rejects_untrusted_host():
    with pytest.raises(ValidationError):
        _stock_image_bytes("https://example.com/untrusted.jpg")
