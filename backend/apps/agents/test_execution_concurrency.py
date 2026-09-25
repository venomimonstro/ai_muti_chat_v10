from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember


@pytest.mark.django_db
def test_agent_busy_in_team_cannot_start_standalone_run():
    user = User.objects.create_user(
        username="busy-agent-single",
        email="busy-agent-single@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(owner=user, name="Worker", objective="Work", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=user, name="Team A", objective="Team work", director=agent)
    AgentTeamMember.objects.create(team=team, agent=agent, role="Director", priority=10, can_delegate=True)
    AgentRun.objects.create(owner=user, team=team, objective="Active team task", state=AgentRun.State.RUNNING)
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(f"/api/v1/agents/{agent.id}/run/", {"objective": "Separate task"}, format="json")

    assert response.status_code == 400
    assert AgentRun.objects.filter(agent=agent).count() == 0


@pytest.mark.django_db
def test_shared_agent_blocks_second_team_run():
    user = User.objects.create_user(
        username="busy-agent-teams",
        email="busy-agent-teams@example.com",
        password="StrongPass123!",
    )
    shared = Agent.objects.create(owner=user, name="Shared", objective="Shared work", status=Agent.Status.ACTIVE)
    other = Agent.objects.create(owner=user, name="Other", objective="Other work", status=Agent.Status.ACTIVE)
    team_a = AgentTeam.objects.create(owner=user, name="Team A", objective="A", director=shared)
    team_b = AgentTeam.objects.create(owner=user, name="Team B", objective="B", director=other)
    AgentTeamMember.objects.create(team=team_a, agent=shared, role="Director A", priority=10, can_delegate=True)
    AgentTeamMember.objects.create(team=team_b, agent=other, role="Director B", priority=10, can_delegate=True)
    AgentTeamMember.objects.create(team=team_b, agent=shared, role="Shared member", priority=20)
    AgentRun.objects.create(owner=user, team=team_a, objective="Active A", state=AgentRun.State.RUNNING)
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.agents.views._enqueue_run") as enqueue:
        response = client.post(f"/api/v1/agent-teams/{team_b.id}/run/", {"objective": "Start B"}, format="json")

    assert response.status_code == 400
    assert AgentRun.objects.filter(team=team_b).count() == 0
    enqueue.assert_not_called()


@pytest.mark.django_db
def test_team_membership_is_frozen_while_team_runs():
    user = User.objects.create_user(
        username="team-membership-freeze",
        email="team-membership-freeze@example.com",
        password="StrongPass123!",
    )
    director = Agent.objects.create(owner=user, name="Director", objective="Direct", status=Agent.Status.ACTIVE)
    worker = Agent.objects.create(owner=user, name="Worker", objective="Work", status=Agent.Status.ACTIVE)
    candidate = Agent.objects.create(owner=user, name="Candidate", objective="Join", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=user, name="Team", objective="Team work", director=director)
    AgentTeamMember.objects.create(team=team, agent=director, role="Director", priority=10, can_delegate=True)
    member = AgentTeamMember.objects.create(team=team, agent=worker, role="Worker", priority=20)
    AgentRun.objects.create(owner=user, team=team, objective="Active", state=AgentRun.State.RUNNING)
    client = APIClient()
    client.force_authenticate(user)

    added = client.post(
        f"/api/v1/agent-teams/{team.id}/members/",
        {"agent": str(candidate.id), "role": "Candidate"},
        format="json",
    )
    assert added.status_code == 400

    changed = client.patch(
        f"/api/v1/agent-teams/{team.id}/members/{member.id}/",
        {"enabled": False},
        format="json",
    )
    assert changed.status_code == 400

    deleted = client.delete(f"/api/v1/agent-teams/{team.id}/members/{member.id}/")
    assert deleted.status_code == 400

    member.refresh_from_db()
    assert member.enabled is True
    assert AgentTeamMember.objects.filter(team=team, agent=candidate).exists() is False
