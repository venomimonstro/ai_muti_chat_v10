from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentApproval, AgentRun


@pytest.mark.django_db
def test_operations_summary_is_owner_scoped_and_reports_costs():
    owner = User.objects.create_user(username="ops-owner", email="ops-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="ops-other", email="ops-other@example.com", password="StrongPass123!")

    agent = Agent.objects.create(
        owner=owner,
        name="Ops agent",
        objective="Work",
        status=Agent.Status.ACTIVE,
    )
    other_agent = Agent.objects.create(
        owner=other,
        name="Other agent",
        objective="Private",
        status=Agent.Status.ACTIVE,
    )

    completed = AgentRun.objects.create(
        owner=owner,
        agent=agent,
        objective="Scheduled work",
        state=AgentRun.State.COMPLETED,
        input_payload={"trigger": "schedule"},
        cost_actual_rub=Decimal("3.2500"),
    )
    waiting = AgentRun.objects.create(
        owner=owner,
        agent=agent,
        objective="Need approval",
        state=AgentRun.State.WAITING_APPROVAL,
        cost_actual_rub=Decimal("1.0000"),
    )
    AgentApproval.objects.create(
        run=waiting,
        requested_by_agent=agent,
        title="Approve",
    )
    AgentRun.objects.create(
        owner=owner,
        agent=agent,
        objective="Failed",
        state=AgentRun.State.FAILED,
        cost_actual_rub=Decimal("0.7500"),
    )
    AgentRun.objects.create(
        owner=other,
        agent=other_agent,
        objective="Foreign cost",
        state=AgentRun.State.COMPLETED,
        input_payload={"trigger": "schedule"},
        cost_actual_rub=Decimal("999.0000"),
    )

    client = APIClient()
    client.force_authenticate(owner)
    response = client.get("/api/v1/agents/operations/summary/")

    assert response.status_code == 200
    assert response.data["agents"] == {"total": 1, "active": 1}
    assert response.data["runs"]["waiting_approval"] == 1
    assert response.data["runs"]["completed_30d"] == 1
    assert response.data["runs"]["failed_30d"] == 1
    assert response.data["approvals"]["pending"] == 1
    assert Decimal(response.data["cost"]["actual_rub_30d"]) == Decimal("5.0000")
    assert Decimal(response.data["cost"]["autonomous_rub_30d"]) == completed.cost_actual_rub
