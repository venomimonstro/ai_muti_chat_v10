import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent


@pytest.mark.django_db
def test_publish_node_without_status_is_saved_as_draft():
    user = User.objects.create_user(username="publish-config", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Publisher",
        objective="Create content",
        tool_policy={"publish": "approval"},
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {
            "graph": {
                "nodes": [
                    {"id": "draft", "title": "Draft", "type": "llm"},
                    {"id": "approve", "title": "Approve", "type": "approval"},
                    {"id": "publish", "title": "Publish", "type": "publish"},
                ],
                "edges": [
                    {"from": "draft", "to": "approve"},
                    {"from": "approve", "to": "publish"},
                ],
            }
        },
        format="json",
    )

    assert response.status_code == 200
    agent.refresh_from_db()
    publish = next(node for node in agent.graph["nodes"] if node["id"] == "publish")
    assert publish["status"] == "draft"


@pytest.mark.django_db
def test_publish_node_rejects_unknown_wordpress_status():
    user = User.objects.create_user(username="publish-config-bad", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Publisher",
        objective="Create content",
        tool_policy={"publish": "approval"},
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {
            "graph": {
                "nodes": [
                    {"id": "approve", "title": "Approve", "type": "approval"},
                    {"id": "publish", "title": "Publish", "type": "publish", "status": "future"},
                ],
                "edges": [{"from": "approve", "to": "publish"}],
            }
        },
        format="json",
    )

    assert response.status_code == 400
    assert "graph" in response.data
