from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from .dev_execution import enrich_changes_with_snapshot


REPOSITORY_CONTEXT = {
    "repository": "owner/repo",
    "default_branch": "main",
    "files": [
        {"path": "backend/app.py", "sha": "abc123", "content": "VALUE = 1\n"},
    ],
}


def test_real_repository_change_is_validated_before_approval():
    with (
        patch("apps.agents.dev_execution.sync_workspace") as sync,
        patch("apps.agents.dev_execution.patch_workspace") as patch_workspace,
        patch(
            "apps.agents.dev_execution.run_workspace_checks",
            return_value={
                "ok": True,
                "checks": [{"command": "python-compile", "ok": True, "output": ""}],
            },
        ) as run_checks,
        patch("apps.agents.dev_execution.destroy_workspace") as destroy,
        patch("apps.agents.dev_execution.sandbox_enabled", return_value=True),
    ):
        result = enrich_changes_with_snapshot(
            [{"path": "backend/app.py", "operation": "update", "content": "VALUE = 2\n"}],
            REPOSITORY_CONTEXT,
        )

    assert result[0]["expected_sha"] == "abc123"
    sync.assert_called_once()
    patch_workspace.assert_called_once()
    run_checks.assert_called_once()
    destroy.assert_called_once()


def test_invalid_change_never_reaches_user_approval_path():
    with (
        patch("apps.agents.dev_execution.sync_workspace"),
        patch("apps.agents.dev_execution.patch_workspace"),
        patch(
            "apps.agents.dev_execution.run_workspace_checks",
            return_value={
                "ok": False,
                "checks": [
                    {
                        "command": "python-compile",
                        "ok": False,
                        "output": "SyntaxError: invalid syntax",
                    }
                ],
            },
        ),
        patch("apps.agents.dev_execution.destroy_workspace") as destroy,
        patch("apps.agents.dev_execution.sandbox_enabled", return_value=True),
    ):
        with pytest.raises(ValidationError, match="Sandbox отклонил изменения"):
            enrich_changes_with_snapshot(
                [{"path": "backend/app.py", "operation": "update", "content": "VALUE =\n"}],
                REPOSITORY_CONTEXT,
            )

    destroy.assert_called_once()


def test_snapshot_only_unit_context_does_not_require_remote_sandbox():
    with patch("apps.agents.dev_execution.sync_workspace") as sync:
        result = enrich_changes_with_snapshot(
            [{"path": "backend/app.py", "operation": "delete"}],
            {"files": REPOSITORY_CONTEXT["files"]},
        )

    assert result[0]["expected_sha"] == "abc123"
    sync.assert_not_called()
