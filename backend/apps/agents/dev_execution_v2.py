"""Sprint 85 project-aware verification compatibility layer."""

from django.core.exceptions import ValidationError

from . import dev_execution as legacy
from .sandbox_client import run_workspace_checks


_original_validate = legacy.validate_changes_in_sandbox


def validate_changes_in_sandbox_v2(changes, *, workspace_id=None, base_files=None, workspace_profile=None):
    result = _original_validate(
        changes,
        workspace_id=workspace_id,
        base_files=base_files,
        workspace_profile=workspace_profile,
    )
    profile = workspace_profile or {}
    if not workspace_id or not base_files or not bool(profile.get("snapshot_complete")):
        return result

    project_checks = [
        str(item)
        for item in (profile.get("project_checks_available") or [])
        if str(item).strip()
    ]
    already = {
        str(item.get("command") or "")
        for item in (result.get("checks") or [])
        if isinstance(item, dict)
    }
    extra = [item for item in project_checks if item not in already]
    if not extra:
        if project_checks:
            result["evidence_level"] = "project_checks_complete_snapshot"
        return result

    project_result = run_workspace_checks(workspace_id=workspace_id, checks=extra)
    combined = list(result.get("checks") or []) + list(project_result.get("checks") or [])
    if not project_result.get("ok"):
        failed = next(
            (item for item in project_result.get("checks", []) if not item.get("ok")),
            project_result,
        )
        raise ValidationError(
            "Project-level проверка отклонила изменения "
            f"({failed.get('command') or 'project-check'}): "
            f"{str(failed.get('output') or failed.get('error') or '')[-4000:]}"
        )

    result["checks"] = combined
    result["ok"] = True
    result["evidence_level"] = "project_checks_complete_snapshot"
    result["project_checks"] = extra
    return result


legacy.validate_changes_in_sandbox = validate_changes_in_sandbox_v2
validate_changes_in_sandbox = validate_changes_in_sandbox_v2
