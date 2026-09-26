from decimal import Decimal

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User

from .limits import agent_commercial_snapshot
from .models import Agent, AgentRun, AgentStepRun


@pytest.mark.django_db
@override_settings(AGENT_MAX_ACTIVE_RUNS_PER_USER=1)
def test_second_agent_run_is_blocked_by_owner_concurrency_limit():
    user = User.objects.create_user(username="limit-user", email="limit@example.com", password="StrongPass123!")
    first = Agent.objects.create(owner=user, name="First", objective="Work", status=Agent.Status.ACTIVE)
    second = Agent.objects.create(owner=user, name="Second", objective="Work", status=Agent.Status.ACTIVE)
    AgentRun.objects.create(owner=user, agent=first, objective="Busy", state=AgentRun.State.RUNNING)

    client = APIClient()
    client.force_authenticate(user)
    response = client.post(f"/api/v1/agents/{second.id}/run/", {"objective": "New work"}, format="json")

    assert response.status_code == 400
    assert "лимит одновременных запусков" in str(response.data).lower()
    assert AgentRun.objects.filter(owner=user, agent=second).count() == 0


@pytest.mark.django_db
@override_settings(AGENT_MAX_ACTIVE_RUNS_PER_USER=3)
def test_usage_endpoint_exposes_budget_and_concurrency_snapshot():
    user = User.objects.create_user(username="usage-user", email="usage@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Usage agent",
        objective="Work",
        status=Agent.Status.ACTIVE,
        max_cost_rub_per_run=Decimal("10.0000"),
        max_cost_rub_per_day=Decimal("20.0000"),
        max_cost_rub_per_month=Decimal("100.0000"),
    )
    completed = AgentRun.objects.create(owner=user, agent=agent, objective="Done", state=AgentRun.State.COMPLETED)
    AgentStepRun.objects.create(
        run=completed,
        agent=agent,
        sequence=1,
        title="Done",
        action_type="llm",
        state=AgentStepRun.State.COMPLETED,
        cost_rub=Decimal("2.5000"),
    )
    AgentRun.objects.create(owner=user, agent=agent, objective="Active", state=AgentRun.State.RUNNING)

    client = APIClient()
    client.force_authenticate(user)
    response = client.get(f"/api/v1/agents/{agent.id}/usage/")

    assert response.status_code == 200
    assert Decimal(response.data["day"]["spent"]) == Decimal("2.5000")
    assert Decimal(response.data["day"]["remaining"]) == Decimal("17.5000")
    assert response.data["concurrency"]["active"] == 1
    assert response.data["concurrency"]["limit"] == 3
    assert response.data["concurrency"]["available"] == 2


@pytest.mark.django_db
def test_commercial_snapshot_never_reports_negative_remaining():
    user = User.objects.create_user(username="negative-user", email="negative@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Budget agent",
        objective="Work",
        status=Agent.Status.ACTIVE,
        max_cost_rub_per_run=Decimal("1.0000"),
        max_cost_rub_per_day=Decimal("1.0000"),
        max_cost_rub_per_month=Decimal("1.0000"),
    )
    run = AgentRun.objects.create(owner=user, agent=agent, objective="Done", state=AgentRun.State.COMPLETED)
    AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=1,
        title="Done",
        action_type="llm",
        state=AgentStepRun.State.COMPLETED,
        cost_rub=Decimal("2.0000"),
    )

    snapshot = agent_commercial_snapshot(agent)
    assert snapshot["day_remaining"] == Decimal("0")
    assert snapshot["month_remaining"] == Decimal("0")
    assert snapshot["effective_remaining"] == Decimal("0")
    assert snapshot["can_start"] is False
