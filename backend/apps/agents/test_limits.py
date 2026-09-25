from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User

from .limits import effective_remaining_budget
from .models import Agent, AgentRun, AgentStepRun


@pytest.mark.django_db
def test_agent_rejects_inconsistent_financial_limits():
    user = User.objects.create_user(
        username="limits-owner",
        email="limits-owner@example.test",
        password="StrongPass123!",
    )
    agent = Agent(
        owner=user,
        name="Budget agent",
        max_cost_rub_per_run=Decimal("20"),
        max_cost_rub_per_day=Decimal("10"),
        max_cost_rub_per_month=Decimal("100"),
    )
    with pytest.raises(ValidationError):
        agent.full_clean()

    agent.max_cost_rub_per_day = Decimal("30")
    agent.max_cost_rub_per_month = Decimal("25")
    with pytest.raises(ValidationError):
        agent.full_clean()


@pytest.mark.django_db
def test_effective_budget_uses_run_day_and_month_minimum():
    user = User.objects.create_user(
        username="limits-spend",
        email="limits-spend@example.test",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Budgeted employee",
        status=Agent.Status.ACTIVE,
        max_cost_rub_per_run=Decimal("10"),
        max_cost_rub_per_day=Decimal("12"),
        max_cost_rub_per_month=Decimal("30"),
    )
    previous = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Previous work",
        state=AgentRun.State.COMPLETED,
        cost_actual_rub=Decimal("8"),
        finished_at=timezone.now(),
    )
    AgentStepRun.objects.create(
        run=previous,
        agent=agent,
        sequence=1,
        node_id="previous",
        title="Previous",
        action_type="llm",
        state=AgentStepRun.State.COMPLETED,
        cost_rub=Decimal("8"),
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )
    current = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Current work",
        state=AgentRun.State.RUNNING,
    )

    remaining, snapshot = effective_remaining_budget(agent, run=current)

    assert snapshot["run_remaining"] == Decimal("10")
    assert snapshot["day_remaining"] == Decimal("4")
    assert snapshot["month_remaining"] == Decimal("22")
    assert remaining == Decimal("4")


@pytest.mark.django_db
def test_current_run_spend_reduces_effective_budget_too():
    user = User.objects.create_user(
        username="limits-current",
        email="limits-current@example.test",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Workflow employee",
        status=Agent.Status.ACTIVE,
        max_cost_rub_per_run=Decimal("10"),
        max_cost_rub_per_day=Decimal("100"),
        max_cost_rub_per_month=Decimal("1000"),
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Multi-step workflow",
        state=AgentRun.State.RUNNING,
    )
    AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=1,
        node_id="one",
        title="One",
        action_type="llm",
        state=AgentStepRun.State.COMPLETED,
        cost_rub=Decimal("7.50"),
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )

    remaining, snapshot = effective_remaining_budget(agent, run=run)

    assert snapshot["run_spend"] == Decimal("7.50")
    assert snapshot["run_remaining"] == Decimal("2.50")
    assert remaining == Decimal("2.50")
