from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.github_integration.models import GitHubInstallation, GitHubRepositoryBinding
from apps.projects.models import Project

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember
from .team_readiness import team_readiness


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


def _dev_team(user, *, write_enabled=True, contents_permission="write"):
    project = Project.objects.create(owner=user, name="Dev project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=880001,
        account_login="owner",
        account_type="User",
        permissions={"contents": contents_permission},
        active=True,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=880002,
        full_name="owner/repo",
        default_branch="main",
        private=True,
        write_enabled=write_enabled,
    )
    roles = ["Engineering Director", "Architecture", "Development", "QA & Security"]
    agents = []
    for index, role in enumerate(roles, start=1):
        agent = Agent.objects.create(
            owner=user,
            project=project,
            name=role,
            role=role,
            objective=f"Work as {role}",
            status=Agent.Status.ACTIVE,
            system_level="balanced",
        )
        agents.append(agent)
    team = AgentTeam.objects.create(
        owner=user,
        project=project,
        name="Dev Team",
        objective="Implement feature safely",
        kind=AgentTeam.Kind.DEVELOPMENT,
        director=agents[0],
        active=True,
    )
    for index, (role, agent) in enumerate(zip(roles, agents), start=1):
        AgentTeamMember.objects.create(
            team=team,
            agent=agent,
            role=role,
            priority=index * 10,
            can_delegate=index == 1,
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


@pytest.mark.django_db
def test_dev_team_is_not_ready_when_github_write_is_disabled():
    user = User.objects.create_user(username="dev-ready-write", password="StrongPass123!")
    team = _dev_team(user, write_enabled=False)

    with patch("apps.agents.team_readiness._model_for", return_value=SimpleNamespace(slug="system-pro")), patch(
        "apps.agents.team_readiness.sandbox_enabled", return_value=True
    ):
        result = team_readiness(team)

    assert result["ready"] is False
    assert result["checks"]["github_write_enabled"] is False
    assert any("рабочую ветку GitHub отключена" in item for item in result["blockers"])


@pytest.mark.django_db
def test_dev_team_is_not_ready_without_provider_write_permission():
    user = User.objects.create_user(username="dev-ready-permission", password="StrongPass123!")
    team = _dev_team(user, contents_permission="read")

    with patch("apps.agents.team_readiness._model_for", return_value=SimpleNamespace(slug="system-pro")), patch(
        "apps.agents.team_readiness.sandbox_enabled", return_value=True
    ):
        result = team_readiness(team)

    assert result["ready"] is False
    assert result["checks"]["github_provider_write"] is False
    assert any("contents:write" in item for item in result["blockers"])


@pytest.mark.django_db
def test_dev_team_is_not_ready_without_sandbox():
    user = User.objects.create_user(username="dev-ready-sandbox", password="StrongPass123!")
    team = _dev_team(user)

    with patch("apps.agents.team_readiness._model_for", return_value=SimpleNamespace(slug="system-pro")), patch(
        "apps.agents.team_readiness.sandbox_enabled", return_value=False
    ):
        result = team_readiness(team)

    assert result["ready"] is False
    assert result["checks"]["sandbox"] is False
    assert any("Sandbox" in item for item in result["blockers"])


@pytest.mark.django_db
def test_dev_team_is_ready_only_with_complete_write_path():
    user = User.objects.create_user(username="dev-ready-complete", password="StrongPass123!")
    team = _dev_team(user)

    with patch("apps.agents.team_readiness._model_for", return_value=SimpleNamespace(slug="system-pro")), patch(
        "apps.agents.team_readiness.sandbox_enabled", return_value=True
    ):
        result = team_readiness(team)

    assert result["ready"] is True
    assert result["checks"]["dev_roles"] is True
    assert result["checks"]["github_provider_write"] is True
    assert result["checks"]["github_write_enabled"] is True
    assert result["checks"]["sandbox"] is True
