import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentTeam, AgentTeamMember


@pytest.mark.django_db
def test_agent_tool_policy_rejects_unknown_and_unsafe_values():
    user = User.objects.create_user(
        username="policy-user",
        email="policy-user@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(owner=user, name="Worker", objective="Work")
    client = APIClient()
    client.force_authenticate(user)

    unknown = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {"tool_policy": {"web": True, "delete_server": True}},
        format="json",
    )
    assert unknown.status_code == 400

    unsafe_publish = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {"tool_policy": {"publish": "always"}},
        format="json",
    )
    assert unsafe_publish.status_code == 400

    unsafe_shell = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {"tool_policy": {"shell": "host"}},
        format="json",
    )
    assert unsafe_shell.status_code == 400


@pytest.mark.django_db
def test_agent_tool_policy_accepts_safe_user_controls():
    user = User.objects.create_user(
        username="policy-safe",
        email="policy-safe@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(owner=user, name="SMM", objective="Create content")
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/agents/{agent.id}/config/",
        {
            "tool_policy": {
                "web": True,
                "files": True,
                "images": False,
                "publish": "approval",
            }
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["tool_policy"] == {
        "web": True,
        "files": True,
        "images": False,
        "publish": "approval",
    }


@pytest.mark.django_db
def test_team_patch_cannot_assign_non_member_as_director():
    user = User.objects.create_user(
        username="director-bypass",
        email="director-bypass@example.com",
        password="StrongPass123!",
    )
    director = Agent.objects.create(owner=user, name="Director", status=Agent.Status.ACTIVE)
    outsider = Agent.objects.create(owner=user, name="Outsider", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=user, name="Team", director=director, objective="Work")
    AgentTeamMember.objects.create(
        team=team,
        agent=director,
        role="Director",
        can_delegate=True,
        enabled=True,
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/agent-teams/{team.id}/",
        {"director": str(outsider.id)},
        format="json",
    )
    assert response.status_code == 400
    team.refresh_from_db()
    assert team.director_id == director.id


@pytest.mark.django_db
def test_team_patch_can_assign_enabled_member_as_director():
    user = User.objects.create_user(
        username="director-member",
        email="director-member@example.com",
        password="StrongPass123!",
    )
    director = Agent.objects.create(owner=user, name="Director", status=Agent.Status.ACTIVE)
    replacement = Agent.objects.create(owner=user, name="Replacement", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=user, name="Team", director=director, objective="Work")
    AgentTeamMember.objects.create(team=team, agent=director, role="Director", enabled=True)
    AgentTeamMember.objects.create(team=team, agent=replacement, role="Lead", enabled=True)
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/agent-teams/{team.id}/",
        {"director": str(replacement.id)},
        format="json",
    )
    assert response.status_code == 200
    team.refresh_from_db()
    assert team.director_id == replacement.id
