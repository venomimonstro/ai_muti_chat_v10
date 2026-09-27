from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember


@pytest.mark.django_db
def test_second_agent_run_request_returns_existing_active_run(django_capture_on_commit_callbacks):
    user = User.objects.create_user(username="agent-dedupe", email="agent-dedupe@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Agent", objective="Do work", status=Agent.Status.ACTIVE)
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.agents.tasks.execute_agent_run_task.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            first = client.post(f"/api/v1/agents/{agent.id}/run/", {"objective": "Task"}, format="json")
            second = client.post(f"/api/v1/agents/{agent.id}/run/", {"objective": "Task"}, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert AgentRun.objects.filter(agent=agent).count() == 1
    delay.assert_called_once()


@pytest.mark.django_db
def test_second_team_run_request_returns_existing_active_run(django_capture_on_commit_callbacks):
    user = User.objects.create_user(username="team-dedupe", email="team-dedupe@example.com", password="StrongPass123!")
    director = Agent.objects.create(owner=user, name="Director", objective="Lead", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=user, name="Team", objective="Do team work", director=director)
    AgentTeamMember.objects.create(
        team=team,
        agent=director,
        role="Director",
        priority=10,
        can_delegate=True,
        enabled=True,
    )
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.agents.team_readiness._model_for", return_value=SimpleNamespace(slug="system-pro")), patch(
        "apps.agents.tasks.execute_agent_run_task.delay"
    ) as delay:
        with django_capture_on_commit_callbacks(execute=True):
            first = client.post(f"/api/v1/agent-teams/{team.id}/run/", {"objective": "Task"}, format="json")
            second = client.post(f"/api/v1/agent-teams/{team.id}/run/", {"objective": "Task"}, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert AgentRun.objects.filter(team=team).count() == 1
    delay.assert_called_once()
