from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from .dev_execution_v2 import validate_changes_in_sandbox_v2


class DevExecutionV2Tests(SimpleTestCase):
    def test_complete_snapshot_runs_project_checks(self):
        base = {
            "ok": True,
            "checks": [{"ok": True, "command": "python-compile", "output": "ok"}],
            "evidence_level": "syntax_complete_snapshot",
        }
        project = {
            "ok": True,
            "checks": [{"ok": True, "command": "django-check", "output": "ok"}],
        }
        with (
            patch("apps.agents.dev_execution_v2._original_validate", return_value=base),
            patch("apps.agents.dev_execution_v2.run_workspace_checks", return_value=project) as runner,
        ):
            result = validate_changes_in_sandbox_v2(
                [{"operation": "update", "path": "app.py", "content": "x=1"}],
                workspace_id="workspace-1234",
                base_files=[{"path": "app.py", "content": "x=0"}],
                workspace_profile={
                    "snapshot_complete": True,
                    "project_checks_available": ["django-check"],
                },
            )
        runner.assert_called_once_with(workspace_id="workspace-1234", checks=["django-check"])
        self.assertEqual(result["evidence_level"], "project_checks_complete_snapshot")
        self.assertEqual([item["command"] for item in result["checks"]], ["python-compile", "django-check"])

    def test_partial_snapshot_never_runs_project_tests(self):
        base = {"ok": True, "checks": [], "evidence_level": "syntax_bounded_snapshot"}
        with (
            patch("apps.agents.dev_execution_v2._original_validate", return_value=base),
            patch("apps.agents.dev_execution_v2.run_workspace_checks") as runner,
        ):
            result = validate_changes_in_sandbox_v2(
                [],
                workspace_id="workspace-1234",
                base_files=[{"path": "app.py", "content": "x=0"}],
                workspace_profile={
                    "snapshot_complete": False,
                    "project_checks_available": ["pytest"],
                },
            )
        runner.assert_not_called()
        self.assertEqual(result["evidence_level"], "syntax_bounded_snapshot")

    def test_failed_project_check_blocks_change_set(self):
        base = {"ok": True, "checks": [], "evidence_level": "syntax_complete_snapshot"}
        project = {
            "ok": False,
            "checks": [{"ok": False, "command": "pytest", "output": "1 failed"}],
        }
        with (
            patch("apps.agents.dev_execution_v2._original_validate", return_value=base),
            patch("apps.agents.dev_execution_v2.run_workspace_checks", return_value=project),
        ):
            with self.assertRaises(ValidationError, msg="project-level failure must be fail-closed"):
                validate_changes_in_sandbox_v2(
                    [],
                    workspace_id="workspace-1234",
                    base_files=[{"path": "pyproject.toml", "content": "[tool.pytest.ini_options]"}],
                    workspace_profile={
                        "snapshot_complete": True,
                        "project_checks_available": ["pytest"],
                    },
                )
