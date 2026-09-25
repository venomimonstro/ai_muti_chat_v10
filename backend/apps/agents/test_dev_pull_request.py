from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.github_integration.models import GitHubInstallation, GitHubRepositoryBinding
from apps.projects.models import Project

from .models import Agent, AgentRun, AgentTeam


def _dev_fixture(username="dev-pr"):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="StrongPass123!",
    )
    project = Project.objects.create(owner=user, name="Repository")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=99101,
        account_login=username,
        account_type="User",
        permissions={"contents": "write", "pull_requests": "write"},
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=99201,
        full_name=f"{username}/repo",
        default_branch="main",
        write_enabled=True,
    )
    director = Agent.objects.create(owner=user, project=project, name="Director", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(
        owner=user,
        project=project,
        name="Dev Team",
        kind=AgentTeam.Kind.DEVELOPMENT,
        director=director,
        objective="Build feature",
    )
    return user, project, team


@pytest.mark.django_db
def test_pull_request_requires_completed_dev_run_with_working_branch():
    user, project, team = _dev_fixture()
    client = APIClient()
    client.force_authenticate(user)

    running = AgentRun.objects.create(
        owner=user,
        team=team,
        project=project,
        objective="Work",
        state=AgentRun.State.RUNNING,
        output_payload={"working_branch": "ai-workspace/run-123"},
    )
    response = client.post(f"/api/v1/agent-runs/{running.id}/pull-request/", {}, format="json")
    assert response.status_code == 400

    completed = AgentRun.objects.create(
        owner=user,
        team=team,
        project=project,
        objective="Work",
        state=AgentRun.State.COMPLETED,
        output_payload={},
    )
    response = client.post(f"/api/v1/agent-runs/{completed.id}/pull-request/", {}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_pull_request_is_owner_scoped_and_idempotent_after_persisted_result():
    user, project, team = _dev_fixture("dev-pr-owner")
    other = User.objects.create_user(
        username="dev-pr-other",
        email="dev-pr-other@example.com",
        password="StrongPass123!",
    )
    run = AgentRun.objects.create(
        owner=user,
        team=team,
        project=project,
        objective="Add billing tests",
        state=AgentRun.State.COMPLETED,
        output_payload={"working_branch": "ai-workspace/run-abcd", "text": "Review passed"},
    )
    client = APIClient()
    client.force_authenticate(other)
    denied = client.post(f"/api/v1/agent-runs/{run.id}/pull-request/", {}, format="json")
    assert denied.status_code == 404

    client.force_authenticate(user)
    pull = {
        "number": 42,
        "html_url": "https://github.com/dev-pr-owner/repo/pull/42",
        "head": "ai-workspace/run-abcd",
        "head_sha": "abc123",
        "base": "main",
        "state": "open",
        "draft": True,
        "existing": False,
    }
    with patch("apps.agents.dev_pr_views.create_pull_request", return_value=pull) as create:
        first = client.post(f"/api/v1/agent-runs/{run.id}/pull-request/", {}, format="json")
    assert first.status_code == 200
    assert first.json()["pull_request"]["number"] == 42
    create.assert_called_once()

    with patch("apps.agents.dev_pr_views.create_pull_request") as create_again:
        second = client.post(f"/api/v1/agent-runs/{run.id}/pull-request/", {}, format="json")
    assert second.status_code == 200
    assert second.json()["created"] is False
    create_again.assert_not_called()


@pytest.mark.django_db
def test_pull_request_rejects_non_development_team():
    user = User.objects.create_user(
        username="normal-team",
        email="normal-team@example.com",
        password="StrongPass123!",
    )
    director = Agent.objects.create(owner=user, name="Director")
    team = AgentTeam.objects.create(owner=user, name="Marketing", director=director, objective="Content")
    run = AgentRun.objects.create(
        owner=user,
        team=team,
        objective="Content",
        state=AgentRun.State.COMPLETED,
        output_payload={"working_branch": "fake"},
    )
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(f"/api/v1/agent-runs/{run.id}/pull-request/", {}, format="json")
    assert response.status_code == 400
