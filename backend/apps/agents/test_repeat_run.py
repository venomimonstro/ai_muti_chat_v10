from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentApproval, AgentRun


@pytest.mark.django_db
def test_repeat_run_creates_fresh_controlled_run_and_deduplicates_second_click():
    user = User.objects.create_user(username="repeat-owner", email="repeat-owner@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Controlled worker",
        objective="Repeat safely",
        status=Agent.Status.ACTIVE,
        autonomy=Agent.Autonomy.CONTROLLED,
    )
    previous = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Original objective",
        state=AgentRun.State.FAILED,
        cost_actual_rub=Decimal("2.5000"),
        error_code="provider_error",
        error_message="Temporary failure",
    )
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.agents.repeat_views.require_agent_ready"):
        first = client.post(f"/api/v1/agent-runs/{previous.id}/repeat/", {}, format="json")
        second = client.post(f"/api/v1/agent-runs/{previous.id}/repeat/", {}, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.data["id"] == second.data["id"]

    previous.refresh_from_db()
    assert previous.state == AgentRun.State.FAILED
    assert previous.cost_actual_rub == Decimal("2.5000")

    repeated = AgentRun.objects.get(pk=first.data["id"])
    assert repeated.id != previous.id
    assert repeated.state == AgentRun.State.WAITING_APPROVAL
    assert repeated.input_payload["trigger"] == "repeat"
    assert repeated.input_payload["previous_run_id"] == str(previous.id)
    assert repeated.cost_actual_rub == Decimal("0")
    assert AgentApproval.objects.filter(run=repeated, status=AgentApproval.Status.PENDING).count() == 1
    assert AgentRun.objects.filter(owner=user, agent=agent).count() == 2


@pytest.mark.django_db
def test_repeat_run_is_owner_scoped():
    owner = User.objects.create_user(username="repeat-a", email="repeat-a@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="repeat-b", email="repeat-b@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Private worker", objective="Private", status=Agent.Status.ACTIVE)
    run = AgentRun.objects.create(owner=owner, agent=agent, objective="Private", state=AgentRun.State.FAILED)
    client = APIClient()
    client.force_authenticate(other)

    response = client.post(f"/api/v1/agent-runs/{run.id}/repeat/", {}, format="json")

    assert response.status_code == 404
    assert AgentRun.objects.filter(owner=owner, agent=agent).count() == 1
