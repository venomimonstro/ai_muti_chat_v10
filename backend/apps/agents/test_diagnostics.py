from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun


@pytest.mark.django_db
def test_diagnostics_are_owner_scoped_and_explain_budget_problem():
    owner = User.objects.create_user(username="diag-owner", email="diag-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="diag-other", email="diag-other@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=owner,
        name="Diagnostic agent",
        objective="Work",
        status=Agent.Status.ACTIVE,
        max_cost_rub_per_run=Decimal("1.0000"),
        max_cost_rub_per_day=Decimal("1.0000"),
        max_cost_rub_per_month=Decimal("1.0000"),
    )
    AgentRun.objects.create(
        owner=owner,
        agent=agent,
        objective="Failed",
        state=AgentRun.State.FAILED,
        error_code="provider_unavailable",
        error_message="Provider unavailable",
    )

    client = APIClient()
    client.force_authenticate(other)
    assert client.get(f"/api/v1/agents/{agent.id}/diagnostics/").status_code == 404

    client.force_authenticate(owner)
    response = client.get(f"/api/v1/agents/{agent.id}/diagnostics/")
    assert response.status_code == 200
    assert response.data["agent_id"] == str(agent.id)
    assert any(item["code"] == "provider_unavailable" for item in response.data["issues"])
