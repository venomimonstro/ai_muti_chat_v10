from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.github_integration.models import GitHubInstallation, GitHubRepositoryBinding
from apps.projects.models import Project

from .dev_execution import apply_approved_changes
from .models import Agent, AgentRun


def _github_project(owner, *, suffix):
    project = Project.objects.create(owner=owner, name=f"Dev project {suffix}")
    installation = GitHubInstallation.objects.create(
        owner=owner,
        installation_id=778899000 + suffix,
        account_login="owner",
        account_type="User",
        permissions={"contents": "write"},
        active=True,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=778899100 + suffix,
        full_name=f"owner/repo-{suffix}",
        default_branch="main",
        private=True,
        write_enabled=True,
    )
    return project


@pytest.mark.django_db
def test_dev_write_stops_between_files_after_cancel():
    owner = User.objects.create_user(
        username="dev-write-cancel",
        email="dev-write-cancel@example.com",
        password="StrongPass123!",
    )
    project = _github_project(owner, suffix=1)

    # This test exercises the write layer directly. The cancellation callback
    # emulates a user stopping the run after the first GitHub file write.
    checks = iter([False, False, False, True])
    should_cancel = lambda: next(checks)
    changes = [
        {"operation": "create", "path": "one.txt", "content": "one", "reason": "first"},
        {"operation": "create", "path": "two.txt", "content": "two", "reason": "second"},
    ]

    with patch("apps.agents.dev_execution.create_repository_branch", return_value={"ref": "ok"}) as create_branch, patch(
        "apps.agents.dev_execution.create_repository_file",
        return_value={"commit_sha": "abc", "content_sha": "def"},
    ) as create_file:
        with pytest.raises(ValidationError, match="остановлен пользователем"):
            apply_approved_changes(
                project=project,
                run_id="00000000-0000-0000-0000-000000000123",
                changes=changes,
                should_cancel=should_cancel,
            )

    create_branch.assert_called_once()
    assert create_file.call_count == 1
    assert create_file.call_args.kwargs["branch"] == "ai-workspace/run-000000000000"


@pytest.mark.django_db
def test_dev_working_branch_is_persisted_before_first_file_write():
    owner = User.objects.create_user(username="dev-branch-marker", password="StrongPass123!")
    project = _github_project(owner, suffix=2)
    agent = Agent.objects.create(
        owner=owner,
        project=project,
        name="Developer",
        role="Software Engineer",
        objective="Write code",
        status=Agent.Status.ACTIVE,
    )
    run = AgentRun.objects.create(
        owner=owner,
        agent=agent,
        project=project,
        objective="Change repository",
        state=AgentRun.State.RUNNING,
        input_payload={"phase": "approved"},
    )
    expected_branch = f"ai-workspace/run-{str(run.id).replace('-', '')[:12]}"

    def assert_marker_before_write(*args, **kwargs):
        run.refresh_from_db()
        assert run.input_payload["phase"] == "writing_changes"
        assert run.input_payload["working_branch"] == expected_branch
        return {"commit_sha": "abc", "content_sha": "def"}

    with patch("apps.agents.dev_execution.create_repository_branch", return_value={"ref": "ok"}), patch(
        "apps.agents.dev_execution.create_repository_file",
        side_effect=assert_marker_before_write,
    ):
        result = apply_approved_changes(
            project=project,
            run_id=run.id,
            changes=[{"operation": "create", "path": "one.txt", "content": "one", "reason": "first"}],
            should_cancel=lambda: False,
        )

    run.refresh_from_db()
    assert result["branch"] == expected_branch
    assert run.input_payload["working_branch"] == expected_branch


@pytest.mark.django_db
def test_dev_write_reads_canceled_run_state_without_callback():
    owner = User.objects.create_user(
        username="dev-write-db-cancel",
        email="dev-write-db-cancel@example.com",
        password="StrongPass123!",
    )
    project = _github_project(owner, suffix=3)
    # AgentRun normally has exactly one subject. For this low-level guard test
    # we mock the state lookup instead of constructing an unrelated Dev Team.
    changes = [{"operation": "create", "path": "one.txt", "content": "one", "reason": "first"}]

    with patch.object(AgentRun.objects, "filter") as filtered, patch(
        "apps.agents.dev_execution.create_repository_branch"
    ) as create_branch:
        filtered.return_value.values_list.return_value.first.return_value = AgentRun.State.CANCELED
        with pytest.raises(ValidationError, match="остановлен до sandbox"):
            apply_approved_changes(
                project=project,
                run_id="00000000-0000-0000-0000-000000000124",
                changes=changes,
            )

    create_branch.assert_not_called()
