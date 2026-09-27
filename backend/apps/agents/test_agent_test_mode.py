import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun


@pytest.mark.django_db
def test_agent_test_mode_has_no_paid_or_external_side_effects():
    user = User.objects.create_user(username="test-mode", email="test-mode@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Publisher",
        objective="Prepare and publish content",
        status=Agent.Status.ACTIVE,
        graph={
            "version": 1,
            "nodes": [
                {"id": "draft", "title": "Черновик", "type": "llm"},
                {"id": "approve", "title": "Согласование", "type": "approval"},
                {"id": "publish", "title": "Публикация", "type": "publish", "status": "draft"},
            ],
            "edges": [{"from": "draft", "to": "approve"}, {"from": "approve", "to": "publish"}],
        },
        tool_policy={"publish": "approval"},
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(f"/api/v1/agents/{agent.id}/test/", {}, format="json")

    assert response.status_code == 200
    assert response.data["mode"] == "simulation"
    assert response.data["paid_calls"] == 0
    assert response.data["external_actions"] == 0
    assert response.data["steps"][1]["status"] == "approval_checkpoint"
    assert response.data["steps"][2]["status"] == "blocked_in_test"
    assert AgentRun.objects.filter(owner=user).count() == 0


@pytest.mark.django_db
def test_agent_test_mode_is_owner_scoped():
    owner = User.objects.create_user(username="test-owner", email="test-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="test-other", email="test-other@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Private", objective="Private")
    client = APIClient()
    client.force_authenticate(other)

    response = client.post(f"/api/v1/agents/{agent.id}/test/", {}, format="json")
    assert response.status_code == 404
