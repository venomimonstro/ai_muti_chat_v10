from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember


def _dev_team():
    user = User.objects.create_user(
        username="dev-guards",
        email="dev-guards@example.com",
        password="StrongPass123!",
    )
    project = Project.objects.create(owner=user, name="Protected Dev project")
    director = Agent.objects.create(
        owner=user,
        project=project,
        name="Engineering Director",
        role="Engineering Director",
        objective="Direct",
        status=Agent.Status.ACTIVE,
    )
    developer = Agent.objects.create(
        owner=user,
        project=project,
        name="Developer",
        role="Software Engineer",
        objective="Develop",
        status=Agent.Status.ACTIVE,
    )
    team = AgentTeam.objects.create(
        owner=user,
        project=project,
        name="Dev Team",
        objective="Develop safely",
        kind=AgentTeam.Kind.DEVELOPMENT,
        director=director,
        max_cost_rub_per_run=Decimal("100"),
    )
    director_member = AgentTeamMember.objects.create(
        team=team,
        agent=director,
        role="Engineering Director",
        priority=10,
        can_delegate=True,
    )
    developer_member = AgentTeamMember.objects.create(
        team=team,
        agent=developer,
        role="Development",
        priority=20,
    )
    return user, project, team, director_member, developer_member


@pytest.mark.django_db
def test_structural_dev_member_cannot_be_renamed_disabled_or_deleted():
    user, _project, team, _director_member, developer_member = _dev_team()
    client = APIClient()
    client.force_authenticate(user)
    url = f"/api/v1/agent-teams/{team.id}/members/{developer_member.id}/"

    renamed = client.patch(url, {"role": "Backend"}, format="json")
    assert renamed.status_code == 400

    disabled = client.patch(url, {"enabled": False}, format="json")
    assert disabled.status_code == 400

    deleted = client.delete(url)
    assert deleted.status_code == 400

    developer_member.refresh_from_db()
    assert developer_member.role == "Development"
    assert developer_member.enabled is True


@pytest.mark.django_db
def test_dev_director_cannot_be_reassigned_through_generic_team_api():
    user, project, team, _director_member, _developer_member = _dev_team()
    replacement = Agent.objects.create(
        owner=user,
        project=project,
        name="Replacement",
        role="Replacement",
        objective="Replace",
        status=Agent.Status.ACTIVE,
    )
    AgentTeamMember.objects.create(team=team, agent=replacement, role="Extra", priority=50)
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        f"/api/v1/agent-teams/{team.id}/director/",
        {"agent": str(replacement.id)},
        format="json",
    )
    assert response.status_code == 400
    team.refresh_from_db()
    assert team.director_id != replacement.id


@pytest.mark.django_db
def test_active_dev_run_blocks_pause_and_budget_change():
    user, project, team, _director_member, _developer_member = _dev_team()
    AgentRun.objects.create(
        owner=user,
        team=team,
        project=project,
        objective="Active work",
        state=AgentRun.State.RUNNING,
    )
    client = APIClient()
    client.force_authenticate(user)
    url = f"/api/v1/agent-teams/{team.id}/"

    paused = client.patch(url, {"active": False}, format="json")
    assert paused.status_code == 400

    budget = client.patch(url, {"max_cost_rub_per_run": "250.0000"}, format="json")
    assert budget.status_code == 400

    team.refresh_from_db()
    assert team.active is True
    assert team.max_cost_rub_per_run == Decimal("100")
