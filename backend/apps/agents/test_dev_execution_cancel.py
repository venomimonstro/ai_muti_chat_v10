from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.github_integration.models import GitHubInstallation, GitHubRepositoryBinding
from apps.projects.models import Project

from .dev_execution import apply_approved_changes
from .models import AgentRun


@pytest.mark.django_db
def test_dev_write_stops_between_files_after_cancel():
    owner = User.objects.create_user(
        username="dev-write-cancel",
        email="dev-write-cancel@example.com",
        password="StrongPass123!",
    )
    project = Project.objects.create(owner=owner, name="Dev project")
    installation = GitHubInstallation.objects.create(
        owner=owner,
        installation_id=778899001,
        account_login="owner",
        account_type="User",
        permissions={"contents": "write"},
        active=True,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=778899002,
        full_name="owner/repo",
        default_branch="main",
        private=True,
        write_enabled=True,
    )

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
def test_dev_write_reads_canceled_run_state_without_callback():
    owner = User.objects.create_user(
        username="dev-write-db-cancel",
        email="dev-write-db-cancel@example.com",
        password="StrongPass123!",
    )
    project = Project.objects.create(owner=owner, name="Dev project")
    installation = GitHubInstallation.objects.create(
        owner=owner,
        installation_id=778899003,
        account_login="owner",
        account_type="User",
        permissions={"contents": "write"},
        active=True,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=778899004,
        full_name="owner/repo",
        default_branch="main",
        private=True,
        write_enabled=True,
    )
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
