import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentVersion


@pytest.mark.django_db
def test_versioned_agent_config_and_restore():
    user = User.objects.create_user(username="agent-config-user", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Writer",
        role="Copywriter",
        objective="Write useful content",
        system_level="balanced",
        graph={
            "version": 1,
            "nodes": [{"id": "draft", "title": "Draft", "type": "llm"}],
            "edges": [],
        },
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {
            "name": "Senior Writer",
            "graph": {
                "version": 2,
                "nodes": [
                    {"id": "research", "title": "Research", "type": "web"},
                    {"id": "draft", "title": "Draft", "type": "llm"},
                ],
                "edges": [{"from": "research", "to": "draft"}],
            },
        },
        format="json",
    )
    assert response.status_code == 200
    version = AgentVersion.objects.get(agent=agent, version=1)
    assert version.snapshot["name"] == "Writer"

    response = client.post(f"/api/v1/agents/{agent.id}/versions/{version.id}/restore/")
    assert response.status_code == 200
    agent.refresh_from_db()
    assert agent.name == "Writer"
    assert agent.graph["nodes"][0]["id"] == "draft"
    assert AgentVersion.objects.filter(agent=agent).count() == 2


@pytest.mark.django_db
def test_agent_graph_rejects_unknown_node_and_foreign_owner():
    owner = User.objects.create_user(username="agent-owner", password="StrongPass123!")
    other = User.objects.create_user(username="agent-other", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Safe agent", objective="Do work", system_level="balanced")

    client = APIClient()
    client.force_authenticate(owner)
    response = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {
            "graph": {
                "nodes": [{"id": "x", "title": "Unsafe", "type": "arbitrary_shell"}],
                "edges": [],
            }
        },
        format="json",
    )
    assert response.status_code == 400
    assert AgentVersion.objects.filter(agent=agent).count() == 0

    client.force_authenticate(other)
    response = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {"name": "Hijacked"},
        format="json",
    )
    assert response.status_code == 404
    agent.refresh_from_db()
    assert agent.name == "Safe agent"
