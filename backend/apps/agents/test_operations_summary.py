from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentApproval, AgentRun
from .schedule_models import AgentSchedule


@pytest.mark.django_db
def test_operations_summary_is_owner_scoped_and_uses_actual_cost():
    owner = User.objects.create_user(username="ops-owner", email="ops-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="ops-other", email="ops-other@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Ops agent", objective="Work", status=Agent.Status.ACTIVE)
    foreign = Agent.objects.create(owner=other, name="Foreign agent", objective="Private", status=Agent.Status.ACTIVE)
    AgentSchedule.objects.create(
        owner=owner,
        agent=agent,
        name="Daily",
        next_run_at="2026-09-26T09:00:00Z",
        enabled=True,
    )
    completed = AgentRun.objects.create(
        owner=owner,
        agent=agent,
        objective="Done",
        state=AgentRun.State.COMPLETED,
        cost_actual_rub=Decimal("12.3400"),
        cost_reserved_rub=Decimal("0"),
        input_payload={"trigger": "schedule", "schedule_id": "x"},
    )
    waiting = AgentRun.objects.create(
        owner=owner,
        agent=agent,
        objective="Approve",
        state=AgentRun.State.WAITING_APPROVAL,
        cost_actual_rub=Decimal("1.0000"),
    )
    AgentApproval.objects.create(
        run=waiting,
        requested_by_agent=agent,
        title="Confirm",
        status=AgentApproval.Status.PENDING,
    )
    AgentRun.objects.create(
        owner=other,
        agent=foreign,
        objective="Foreign",
        state=AgentRun.State.FAILED,
        cost_actual_rub=Decimal("999.0000"),
    )

    client = APIClient()
    client.force_authenticate(owner)
    response = client.get("/api/v1/agents/operations/summary/")

    assert response.status_code == 200
    assert response.data["agents"] == {"total": 1, "active": 1}
    assert response.data["schedules"]["enabled"] == 1
    assert response.data["runs"]["waiting_approval"] == 1
    assert response.data["runs"]["completed_30d"] == 1
    assert response.data["runs"]["failed_30d"] == 0
    assert response.data["approvals"]["pending"] == 1
    assert response.data["cost"]["actual_rub_30d"] == "13.3400"
    assert response.data["cost"]["autonomous_rub_30d"] == "12.3400"
    assert completed.owner_id == owner.id
