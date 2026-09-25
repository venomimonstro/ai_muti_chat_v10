from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentApproval, AgentRun


@pytest.mark.django_db
def test_visual_publish_workflow_is_blocked_before_run_without_wordpress():
    user = User.objects.create_user(username="ready-publish", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Publisher",
        objective="Create article",
        status=Agent.Status.ACTIVE,
        autonomy=Agent.Autonomy.SEMI_AUTONOMOUS,
        tool_policy={"publish": "approval"},
        graph={
            "version": 1,
            "nodes": [
                {"id": "draft", "title": "Draft", "type": "llm"},
                {"id": "approve", "title": "Approve", "type": "approval"},
                {"id": "publish", "title": "Publish", "type": "publish", "status": "draft"},
            ],
            "edges": [
                {"from": "draft", "to": "approve"},
                {"from": "approve", "to": "publish"},
            ],
        },
    )
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.agents.readiness._model_for", return_value=SimpleNamespace(slug="system-pro")):
        readiness = client.get(f"/api/v1/agents/{agent.id}/readiness/")
        started = client.post(f"/api/v1/agents/{agent.id}/run/", {}, format="json")

    assert readiness.status_code == 200
    assert readiness.data["ready"] is False
    assert any("WordPress" in item for item in readiness.data["blockers"])
    assert started.status_code == 400
    assert AgentRun.objects.filter(agent=agent).count() == 0


@pytest.mark.django_db
def test_readiness_is_owner_scoped():
    owner = User.objects.create_user(username="ready-owner", password="StrongPass123!")
    other = User.objects.create_user(username="ready-other", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Private agent", objective="Work")
    client = APIClient()
    client.force_authenticate(other)

    response = client.get(f"/api/v1/agents/{agent.id}/readiness/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_approval_resume_rechecks_readiness_and_keeps_approval_pending_on_failure():
    user = User.objects.create_user(username="ready-approval", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Controlled",
        objective="Prepare result",
        status=Agent.Status.ACTIVE,
        autonomy=Agent.Autonomy.CONTROLLED,
        graph={
            "version": 1,
            "nodes": [{"id": "work", "title": "Work", "type": "llm"}],
            "edges": [],
        },
    )
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.agents.readiness._model_for", return_value=SimpleNamespace(slug="system-pro")):
        started = client.post(f"/api/v1/agents/{agent.id}/run/", {}, format="json")
    assert started.status_code == 201
    run = AgentRun.objects.get(pk=started.data["id"])
    approval = AgentApproval.objects.get(run=run)
    assert run.state == AgentRun.State.WAITING_APPROVAL

    with patch(
        "apps.agents.readiness._model_for",
        side_effect=DjangoValidationError("Нет подключённой модели"),
    ), patch("apps.agents.tasks.execute_agent_run_task.delay") as delay:
        response = client.post(
            f"/api/v1/agent-runs/{run.id}/approvals/{approval.id}/decision/",
            {"decision": "approved"},
            format="json",
        )

    assert response.status_code == 400
    approval.refresh_from_db()
    run.refresh_from_db()
    assert approval.status == AgentApproval.Status.PENDING
    assert run.state == AgentRun.State.WAITING_APPROVAL
    delay.assert_not_called()
