import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent


def _client_and_agent(username):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Workflow agent",
        role="Content specialist",
        objective="Prepare content",
        status=Agent.Status.DRAFT,
        graph={"version": 1, "nodes": [{"id": "one", "title": "Start", "type": "llm"}], "edges": []},
    )
    client = APIClient()
    client.force_authenticate(user)
    return client, agent


@pytest.mark.django_db
def test_config_accepts_condition_notify_wait_and_prompts():
    client, agent = _client_and_agent("graph-config-ok")
    graph = {
        "version": 2,
        "nodes": [
            {"id": "research", "title": "Research", "type": "research", "prompt": "Find current facts"},
            {
                "id": "check",
                "title": "Check",
                "type": "condition",
                "condition_source": "previous_text",
                "operator": "contains",
                "value": "ready",
                "on_true": "notify",
                "on_false": "wait",
            },
            {"id": "wait", "title": "Wait", "type": "wait", "wait_minutes": 30},
            {
                "id": "notify",
                "title": "Notify",
                "type": "notify",
                "notification_title": "Done",
                "message": "Check the result",
            },
            {"id": "finish", "title": "Finish", "type": "finish"},
        ],
        "edges": [
            {"from": "research", "to": "check"},
            {"from": "check", "to": "wait"},
            {"from": "wait", "to": "notify"},
            {"from": "notify", "to": "finish"},
        ],
    }

    response = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {"graph": graph},
        format="json",
    )

    assert response.status_code == 200
    agent.refresh_from_db()
    assert agent.graph["nodes"][0]["prompt"] == "Find current facts"
    assert agent.graph["nodes"][1]["on_true"] == "notify"
    assert agent.graph["nodes"][2]["wait_minutes"] == 30


@pytest.mark.django_db
def test_config_rejects_backward_condition_route():
    client, agent = _client_and_agent("graph-config-backward")
    graph = {
        "nodes": [
            {"id": "start", "title": "Start", "type": "llm", "prompt": "Work"},
            {
                "id": "check",
                "title": "Check",
                "type": "condition",
                "condition_source": "objective",
                "operator": "contains",
                "value": "yes",
                "on_true": "start",
            },
            {"id": "finish", "title": "Finish", "type": "finish"},
        ],
        "edges": [
            {"from": "start", "to": "check"},
            {"from": "check", "to": "finish"},
        ],
    }

    response = client.patch(f"/api/v1/agents/{agent.id}/config/", {"graph": graph}, format="json")

    assert response.status_code == 400
    assert "более поздний" in str(response.data).lower()


@pytest.mark.django_db
def test_config_rejects_backward_edge_and_excessive_wait():
    client, agent = _client_and_agent("graph-config-edge")
    cyclic = {
        "nodes": [
            {"id": "one", "title": "One", "type": "llm", "prompt": "Work"},
            {"id": "two", "title": "Two", "type": "llm", "prompt": "Review"},
        ],
        "edges": [{"from": "two", "to": "one"}],
    }
    response = client.patch(f"/api/v1/agents/{agent.id}/config/", {"graph": cyclic}, format="json")
    assert response.status_code == 400
    assert "циклические" in str(response.data).lower()

    too_long = {
        "nodes": [{"id": "wait", "title": "Wait", "type": "wait", "wait_minutes": 10081}],
        "edges": [],
    }
    response = client.patch(f"/api/v1/agents/{agent.id}/config/", {"graph": too_long}, format="json")
    assert response.status_code == 400
    assert "7 дней" in str(response.data)
