from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember


def _team(user, *, name="Ready team"):
    director = Agent.objects.create(
        owner=user,
        name="Director",
        role="Director",
        objective="Lead work",
        status=Agent.Status.ACTIVE,
        system_level="balanced",
    )
    team = AgentTeam.objects.create(
        owner=user,
        name=name,
        objective="Prepare result",
        director=director,
        active=True,
    )
    AgentTeamMember.objects.create(
        team=team,
        agent=director,
        role="Director",
        priority=10,
        can_delegate=True,
        enabled=True,
    )
    return team


@pytest.mark.django_db
def test_team_readiness_is_owner_scoped():
    owner = User.objects.create_user(username="team-ready-owner", password="StrongPass123!")
    other = User.objects.create_user(username="team-ready-other", password="StrongPass123!")
    team = _team(owner)
    client = APIClient()
    client.force_authenticate(other)

    response = client.get(f"/api/v1/agent-teams/{team.id}/readiness/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_team_run_is_blocked_before_queue_when_member_model_is_unavailable():
    user = User.objects.create_user(username="team-ready-block", password="StrongPass123!")
    team = _team(user)
    client = APIClient()
    client.force_authenticate(user)

    with patch(
        "apps.agents.team_readiness._model_for",
        side_effect=DjangoValidationError("Нет подключённой модели"),
    ), patch("apps.agents.tasks.execute_agent_run_task.delay") as delay:
        response = client.post(f"/api/v1/agent-teams/{team.id}/run/", {}, format="json")

    assert response.status_code == 400
    assert AgentRun.objects.filter(team=team).count() == 0
    delay.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_team_double_launch_returns_same_active_run_and_queues_once():
    user = User.objects.create_user(username="team-ready-run", password="StrongPass123!")
    team = _team(user)
    client = APIClient()
    client.force_authenticate(user)

    with patch(
        "apps.agents.team_readiness._model_for",
        return_value=SimpleNamespace(slug="system-pro"),
    ), patch("apps.agents.tasks.execute_agent_run_task.delay") as delay:
        first = client.post(f"/api/v1/agent-teams/{team.id}/run/", {}, format="json")
        second = client.post(f"/api/v1/agent-teams/{team.id}/run/", {}, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.data["id"] == second.data["id"]
    assert AgentRun.objects.filter(team=team).count() == 1
    delay.assert_called_once_with(first.data["id"])
