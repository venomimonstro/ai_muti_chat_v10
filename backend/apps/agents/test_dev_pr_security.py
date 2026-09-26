from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.github_integration.models import GitHubInstallation, GitHubRepositoryBinding
from apps.projects.models import Project

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember


def _dev_run(owner, *, branch):
    project = Project.objects.create(owner=owner, name="Dev project")
    installation = GitHubInstallation.objects.create(
        owner=owner,
        installation_id=987654321,
        account_login="owner",
        account_type="User",
        permissions={"contents": "write", "pull_requests": "write"},
        active=True,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=123456789,
        full_name="owner/repo",
        default_branch="main",
        private=True,
        write_enabled=True,
    )
    director = Agent.objects.create(
        owner=owner,
        project=project,
        name="Engineering Director",
        role="Engineering Director",
        objective="Lead development",
        status=Agent.Status.ACTIVE,
        tool_policy={"github": True, "approve": True},
    )
    team = AgentTeam.objects.create(
        owner=owner,
        project=project,
        name="Dev Team",
        objective="Build safely",
        kind=AgentTeam.Kind.DEVELOPMENT,
        director=director,
    )
    AgentTeamMember.objects.create(
        team=team,
        agent=director,
        role="Engineering Director",
        priority=10,
        can_delegate=True,
    )
    run = AgentRun.objects.create(
        owner=owner,
        team=team,
        project=project,
        objective="Implement feature",
        state=AgentRun.State.COMPLETED,
        output_payload={"working_branch": branch, "text": "QA passed"},
    )
    return run


@pytest.mark.django_db
def test_dev_pr_rejects_default_branch():
    owner = User.objects.create_user(
        username="dev-pr-default",
        email="dev-pr-default@example.com",
        password="StrongPass123!",
    )
    run = _dev_run(owner, branch="main")
    client = APIClient()
    client.force_authenticate(owner)

    with patch("apps.agents.dev_pr_views.create_pull_request") as create_pr:
        response = client.post(f"/api/v1/agent-runs/{run.id}/pull-request/", {}, format="json")

    assert response.status_code == 400
    create_pr.assert_not_called()


@pytest.mark.django_db
def test_dev_pr_rejects_branch_from_another_run():
    owner = User.objects.create_user(
        username="dev-pr-foreign",
        email="dev-pr-foreign@example.com",
        password="StrongPass123!",
    )
    run = _dev_run(owner, branch="ai-workspace/run-deadbeef1234")
    client = APIClient()
    client.force_authenticate(owner)

    with patch("apps.agents.dev_pr_views.create_pull_request") as create_pr:
        response = client.post(f"/api/v1/agent-runs/{run.id}/pull-request/", {}, format="json")

    assert response.status_code == 400
    create_pr.assert_not_called()


@pytest.mark.django_db
def test_dev_pr_accepts_only_current_run_branch_and_is_idempotent():
    owner = User.objects.create_user(
        username="dev-pr-valid",
        email="dev-pr-valid@example.com",
        password="StrongPass123!",
    )
    project = Project.objects.create(owner=owner, name="Dev project")
    installation = GitHubInstallation.objects.create(
        owner=owner,
        installation_id=987654322,
        account_login="owner",
        account_type="User",
        permissions={"contents": "write", "pull_requests": "write"},
        active=True,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=123456790,
        full_name="owner/repo",
        default_branch="main",
        private=True,
        write_enabled=True,
    )
    director = Agent.objects.create(
        owner=owner,
        project=project,
        name="Engineering Director",
        role="Engineering Director",
        objective="Lead development",
        status=Agent.Status.ACTIVE,
        tool_policy={"github": True, "approve": True},
    )
    team = AgentTeam.objects.create(
        owner=owner,
        project=project,
        name="Dev Team",
        objective="Build safely",
        kind=AgentTeam.Kind.DEVELOPMENT,
        director=director,
    )
    AgentTeamMember.objects.create(team=team, agent=director, role="Engineering Director", priority=10)
    run = AgentRun.objects.create(
        owner=owner,
        team=team,
        project=project,
        objective="Implement feature",
        state=AgentRun.State.COMPLETED,
    )
    branch = f"ai-workspace/run-{str(run.id).replace('-', '')[:12]}"
    run.output_payload = {"working_branch": branch, "text": "QA passed"}
    run.save(update_fields=["output_payload", "updated_at"])
    client = APIClient()
    client.force_authenticate(owner)
    pull = {"number": 7, "html_url": "https://github.com/owner/repo/pull/7", "existing": False}

    with patch("apps.agents.dev_pr_views.create_pull_request", return_value=pull) as create_pr:
        first = client.post(f"/api/v1/agent-runs/{run.id}/pull-request/", {}, format="json")
        second = client.post(f"/api/v1/agent-runs/{run.id}/pull-request/", {}, format="json")

    assert first.status_code == 200
    assert first.data["created"] is True
    assert second.status_code == 200
    assert second.data["created"] is False
    create_pr.assert_called_once()
