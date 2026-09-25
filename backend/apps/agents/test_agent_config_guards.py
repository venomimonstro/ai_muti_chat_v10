import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun, AgentVersion


@pytest.mark.django_db
def test_agent_config_is_frozen_during_active_run():
    user = User.objects.create_user(
        username="agent-config-guard",
        email="agent-config-guard@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Protected agent",
        role="Worker",
        objective="Work safely",
        status=Agent.Status.ACTIVE,
        graph={"version": 1, "nodes": [{"id": "a", "title": "Work", "type": "llm"}], "edges": []},
    )
    AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Active work",
        state=AgentRun.State.RUNNING,
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {"instructions": "Changed while running"},
        format="json",
    )

    assert response.status_code == 400
    agent.refresh_from_db()
    assert agent.instructions == ""
    assert AgentVersion.objects.filter(agent=agent).count() == 0


@pytest.mark.django_db
def test_agent_version_restore_is_frozen_during_active_run():
    user = User.objects.create_user(
        username="agent-version-guard",
        email="agent-version-guard@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Versioned agent",
        role="Worker",
        objective="Work safely",
        status=Agent.Status.ACTIVE,
    )
    version = AgentVersion.objects.create(
        agent=agent,
        version=1,
        created_by=user,
        snapshot={
            "name": "Old name",
            "role": "Old role",
            "objective": "Old objective",
            "instructions": "Old instructions",
            "autonomy": Agent.Autonomy.CONTROLLED,
            "status": Agent.Status.ACTIVE,
            "system_level": "balanced",
            "tool_policy": {},
            "graph": {},
            "memory_policy": {},
            "max_cost_rub_per_run": "10.0000",
            "max_cost_rub_per_day": "100.0000",
            "max_cost_rub_per_month": "1500.0000",
            "max_steps": 50,
            "max_tool_calls": 50,
            "max_handoffs": 20,
            "max_retries_per_step": 3,
            "max_runtime_seconds": 3600,
        },
    )
    AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Active work",
        state=AgentRun.State.WAITING_APPROVAL,
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        f"/api/v1/agents/{agent.id}/versions/{version.id}/restore/",
        {},
        format="json",
    )

    assert response.status_code == 400
    agent.refresh_from_db()
    assert agent.name == "Versioned agent"
