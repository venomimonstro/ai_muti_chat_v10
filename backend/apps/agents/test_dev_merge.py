from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.github_integration.models import GitHubInstallation, GitHubRepositoryBinding
from apps.projects.models import Project

from .models import Agent, AgentRun, AgentTeam


def _fixture(username="dev-merge"):
    user = User.objects.create_user(username=username, password="StrongPass123!")
    project = Project.objects.create(owner=user, name="Repository")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=660001,
        account_login=username,
        account_type="User",
        permissions={"contents": "write", "pull_requests": "write"},
        active=True,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=660002,
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
        objective="Build safely",
    )
    run = AgentRun.objects.create(
        owner=user,
        team=team,
        project=project,
        objective="Build safely",
        state=AgentRun.State.COMPLETED,
    )
    branch = f"ai-workspace/run-{str(run.id).replace('-', '')[:12]}"
    pull = {
        "number": 17,
        "html_url": f"https://github.com/{username}/repo/pull/17",
        "head": branch,
        "head_sha": "headsha123",
        "base": "main",
        "state": "open",
        "draft": True,
    }
    run.output_payload = {"working_branch": branch, "pull_request": pull}
    run.save(update_fields=["output_payload", "updated_at"])
    return user, run, pull


def _github_pull(pull, *, merged=False, head_sha=None):
    return {
        "number": pull["number"],
        "state": "closed" if merged else "open",
        "merged": merged,
        "merged_at": "2026-09-26T10:00:00Z" if merged else None,
        "merge_commit_sha": "merge123" if merged else None,
        "head": {"ref": pull["head"], "sha": head_sha or pull["head_sha"]},
        "base": {"ref": pull["base"]},
    }


@pytest.mark.django_db
def test_merge_requires_explicit_confirmation():
    user, run, _pull = _fixture()
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(f"/api/v1/agent-runs/{run.id}/pull-request/merge/", {}, format="json")

    assert response.status_code == 400
    assert "confirm_merge" in str(response.data)


@pytest.mark.django_db
def test_merge_is_owner_scoped():
    user, run, _pull = _fixture("dev-merge-owner")
    other = User.objects.create_user(username="dev-merge-other", password="StrongPass123!")
    client = APIClient()
    client.force_authenticate(other)

    response = client.post(
        f"/api/v1/agent-runs/{run.id}/pull-request/merge/",
        {"confirm_merge": True},
        format="json",
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_merge_rejects_non_completed_run():
    user, run, _pull = _fixture("dev-merge-state")
    run.state = AgentRun.State.FAILED
    run.save(update_fields=["state", "updated_at"])
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        f"/api/v1/agent-runs/{run.id}/pull-request/merge/",
        {"confirm_merge": True},
        format="json",
    )

    assert response.status_code == 400
    assert "успешно завершённого" in str(response.data)


@pytest.mark.django_db
def test_merge_blocks_if_pull_request_head_sha_changed():
    user, run, pull = _fixture("dev-merge-sha")
    client = APIClient()
    client.force_authenticate(user)

    with patch(
        "apps.agents.dev_merge_views.get_pull_request",
        return_value=_github_pull(pull, head_sha="different-sha"),
    ), patch("apps.agents.dev_merge_views.merge_pull_request") as merge:
        response = client.post(
            f"/api/v1/agent-runs/{run.id}/pull-request/merge/",
            {"confirm_merge": True},
            format="json",
        )

    assert response.status_code == 400
    assert "новые commits" in str(response.data)
    merge.assert_not_called()


@pytest.mark.django_db
def test_merge_is_persisted_and_repeated_request_is_idempotent():
    user, run, pull = _fixture("dev-merge-ok")
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.agents.dev_merge_views.get_pull_request", return_value=_github_pull(pull)), patch(
        "apps.agents.dev_merge_views.merge_pull_request",
        return_value={"merged": True, "sha": "mergedsha", "message": "ok", "number": 17},
    ) as merge:
        first = client.post(
            f"/api/v1/agent-runs/{run.id}/pull-request/merge/",
            {"confirm_merge": True, "merge_method": "squash"},
            format="json",
        )

    assert first.status_code == 200
    assert first.data["pull_request"]["merged"] is True
    assert first.data["pull_request"]["merge_sha"] == "mergedsha"
    merge.assert_called_once()

    with patch("apps.agents.dev_merge_views.get_pull_request") as get_again, patch(
        "apps.agents.dev_merge_views.merge_pull_request"
    ) as merge_again:
        second = client.post(
            f"/api/v1/agent-runs/{run.id}/pull-request/merge/",
            {"confirm_merge": True},
            format="json",
        )

    assert second.status_code == 200
    assert second.data["created"] is False
    get_again.assert_not_called()
    merge_again.assert_not_called()


@pytest.mark.django_db
def test_merge_recovers_when_github_already_merged_after_previous_crash():
    user, run, pull = _fixture("dev-merge-recover")
    client = APIClient()
    client.force_authenticate(user)

    with patch(
        "apps.agents.dev_merge_views.get_pull_request",
        return_value=_github_pull(pull, merged=True),
    ), patch("apps.agents.dev_merge_views.merge_pull_request") as merge:
        response = client.post(
            f"/api/v1/agent-runs/{run.id}/pull-request/merge/",
            {"confirm_merge": True},
            format="json",
        )

    assert response.status_code == 200
    assert response.data["created"] is False
    assert response.data["pull_request"]["merged"] is True
    assert response.data["pull_request"]["merge_sha"] == "merge123"
    merge.assert_not_called()
    run.refresh_from_db()
    assert run.output_payload["pull_request"]["merged"] is True
