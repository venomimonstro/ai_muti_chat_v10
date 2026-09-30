import uuid

from django.core.exceptions import ValidationError

from apps.github_integration.branching import create_repository_branch
from apps.github_integration.models import GitHubOperationLog
from apps.github_integration.mutations import create_repository_file, delete_repository_file
from apps.github_integration.services import write_repository_file

from .dev_context import build_repository_context
from .dev_security import secure_change_set
from .sandbox_client import (
    destroy_workspace,
    patch_workspace,
    run_sandbox,
    run_workspace_checks,
    sandbox_enabled,
    sync_workspace,
)


MAX_EXECUTION_CHANGES = 12


def enrich_changes_with_snapshot(changes, repository_context):
    if len(changes) > MAX_EXECUTION_CHANGES:
        raise ValidationError(
            f"Dev Studio предложил слишком много изменений за один запуск: максимум {MAX_EXECUTION_CHANGES}"
        )
    visible = {
        str(item.get("path") or ""): item
        for item in (repository_context.get("files") or [])
        if item.get("path")
    }
    result = []
    for change in changes:
        item = dict(change)
        if item["operation"] in {"update", "delete"}:
            source = visible.get(item["path"])
            if not source:
                raise ValidationError(
                    f"Нельзя {item['operation']} {item['path']}: файл не был прочитан Developer перед предложением изменения"
                )
            expected_sha = str(source.get("sha") or "").strip()
            if not expected_sha:
                raise ValidationError(f"Для {item['path']} отсутствует исходный SHA")
            item["expected_sha"] = expected_sha
        result.append(item)

    result = secure_change_set(result)
    if repository_context.get("repository"):
        workspace_id = f"preview-{uuid.uuid4().hex[:24]}"
        try:
            validate_changes_in_sandbox(
                result,
                workspace_id=workspace_id,
                base_files=repository_context.get("files") or [],
                workspace_profile=repository_context.get("workspace_profile") or {},
            )
        finally:
            if sandbox_enabled():
                try:
                    destroy_workspace(workspace_id=workspace_id)
                except Exception:
                    pass
    return result


def _sandbox_checks(changes):
    paths = [str(item["path"]).lower() for item in changes]
    checks = []
    if any(path.endswith(".json") for path in paths):
        checks.append("json-check")
    if any(path.endswith((".js", ".mjs", ".cjs")) for path in paths):
        checks.append("node-check")
    if any(path.endswith(".py") for path in paths):
        checks.append("python-compile")
    return checks


def _legacy_sandbox_result(changes, checks):
    if len(checks) != 1:
        return None
    files = [
        {"path": item["path"], "content": item["content"]}
        for item in changes
        if item.get("operation") in {"create", "update"}
    ]
    return run_sandbox(command=checks[0], files=files)


def _workspace_operations(changes):
    operations = []
    for item in changes:
        operation = item.get("operation") or "update"
        payload = {"operation": operation, "path": item["path"]}
        if operation in {"create", "update"}:
            payload["content"] = item.get("content", "")
        operations.append(payload)
    return operations


def _verification_metadata(workspace_profile, *, checks):
    profile = workspace_profile or {}
    snapshot_complete = bool(profile.get("snapshot_complete"))
    if not checks:
        evidence_level = "structural"
    elif snapshot_complete:
        evidence_level = "syntax_complete_snapshot"
    else:
        evidence_level = "syntax_bounded_snapshot"
    return {
        "evidence_level": evidence_level,
        "snapshot_complete": snapshot_complete,
        "repository_evidence_level": str(profile.get("evidence_level") or "unknown"),
        "project_types": {
            "python": bool(profile.get("python_project")),
            "django": bool(profile.get("django_project")),
            "node": bool(profile.get("node_project")),
        },
    }


def validate_changes_in_sandbox(changes, *, workspace_id=None, base_files=None, workspace_profile=None):
    checks = _sandbox_checks(changes)
    metadata = _verification_metadata(workspace_profile, checks=checks)
    if not checks:
        return {
            "ok": True,
            "command": "structural-validation",
            "checks": [],
            "output": "Для изменённых типов файлов нет безопасной исполняемой проверки; выполнена структурная валидация.",
            **metadata,
        }
    if not sandbox_enabled():
        raise ValidationError("Sandbox не настроен; запись кода заблокирована")

    if workspace_id and base_files is not None:
        files = [
            {"path": str(item.get("path") or ""), "content": str(item.get("content") or "")}
            for item in base_files
            if item.get("path") and isinstance(item.get("content"), str)
        ]
        sync_workspace(workspace_id=workspace_id, files=files, reset=True)
        operations = _workspace_operations(changes)
        if operations:
            patch_workspace(workspace_id=workspace_id, operations=operations)
        result = run_workspace_checks(workspace_id=workspace_id, checks=checks)
    else:
        result = _legacy_sandbox_result(changes, checks)
        if result is None:
            raise ValidationError("Для многошаговой проверки требуется Dev Workspace")

    if not result.get("ok"):
        failed = next((item for item in result.get("checks", []) if not item.get("ok")), result)
        raise ValidationError(
            "Sandbox отклонил изменения "
            f"({failed.get('command') or 'workspace-check'}): "
            f"{str(failed.get('output') or failed.get('error') or '')[-4000:]}"
        )
    result.update(metadata)
    return result


def _default_should_cancel(run_id):
    from .models import AgentRun

    state = AgentRun.objects.filter(pk=run_id).values_list("state", flat=True).first()
    return state == AgentRun.State.CANCELED


def _persist_run_execution_state(run_id, **values):
    from .models import AgentRun

    run = AgentRun.objects.filter(pk=run_id).only("id", "input_payload").first()
    if run is None:
        return False
    payload = dict(run.input_payload or {})
    payload.update(values)
    run.input_payload = payload
    run.save(update_fields=["input_payload", "updated_at"])
    return True


def _persist_working_branch(run_id, branch_name):
    return _persist_run_execution_state(run_id, phase="writing_changes", working_branch=branch_name)


def apply_approved_changes(*, project, run_id, changes, should_cancel=None):
    if not changes:
        return {"branch": None, "changes": [], "sandbox": None}
    try:
        binding = project.github_repository
    except Exception as exc:
        raise ValidationError("У проекта отсутствует GitHub repository binding") from exc
    if not binding.write_enabled:
        raise ValidationError("Для проекта не разрешена запись в GitHub")

    cancel_check = should_cancel or (lambda: _default_should_cancel(run_id))
    if cancel_check():
        raise ValidationError("Dev Studio остановлен до sandbox/GitHub write")

    workspace_id = f"devrun-{str(run_id).replace('-', '')[:24]}"
    checks = _sandbox_checks(changes)
    if checks:
        repository_context = build_repository_context(project, ref=binding.default_branch)
        _persist_run_execution_state(run_id, phase="validating_changes", workspace_id=workspace_id)
        sandbox_result = validate_changes_in_sandbox(
            changes,
            workspace_id=workspace_id,
            base_files=repository_context.get("files") or [],
            workspace_profile=repository_context.get("workspace_profile") or {},
        )
    else:
        sandbox_result = validate_changes_in_sandbox(changes)

    if cancel_check():
        raise ValidationError("Dev Studio остановлен после sandbox и до создания рабочей ветки")

    branch_name = f"ai-workspace/run-{str(run_id).replace('-', '')[:12]}"
    create_repository_branch(binding, branch_name, from_ref=binding.default_branch)
    _persist_working_branch(run_id, branch_name)
    applied = []
    for index, change in enumerate(changes, start=1):
        if cancel_check():
            raise ValidationError(
                f"Dev Studio остановлен пользователем. Уже записано файлов: {len(applied)}. "
                f"Изменения остались только в изолированной ветке {branch_name}."
            )
        message = f"AI Workspace run {str(run_id)[:8]}: {change.get('reason') or change['path']}"
        try:
            if change["operation"] == "update":
                result = write_repository_file(
                    binding,
                    change["path"],
                    content=change["content"],
                    expected_sha=change["expected_sha"],
                    message=message,
                    branch=branch_name,
                )
            elif change["operation"] == "create":
                result = create_repository_file(
                    binding,
                    change["path"],
                    content=change["content"],
                    message=message,
                    branch=branch_name,
                )
            elif change["operation"] == "delete":
                result = delete_repository_file(
                    binding,
                    change["path"],
                    expected_sha=change["expected_sha"],
                    message=message,
                    branch=branch_name,
                )
            else:
                raise ValidationError("Неподдерживаемая операция изменения")
        except Exception as exc:
            GitHubOperationLog.objects.create(
                actor=project.owner,
                binding=binding,
                action=f"agent_{change.get('operation', 'unknown')}_file",
                path=str(change.get("path") or "")[:1024],
                branch=branch_name[:255],
                success=False,
                metadata={
                    "run_id": str(run_id),
                    "sequence": index,
                    "applied_before_failure": len(applied),
                    "workspace_id": workspace_id if checks else "",
                    "risk_flags": change.get("risk_flags") or [],
                    "error": str(exc)[:2000],
                },
            )
            raise ValidationError(
                f"GitHub write остановлен на файле {change.get('path')}. "
                f"Уже записано файлов: {len(applied)}. Рабочая ветка: {branch_name}. Причина: {exc}"
            ) from exc

        GitHubOperationLog.objects.create(
            actor=project.owner,
            binding=binding,
            action=f"agent_{change['operation']}_file",
            path=change["path"][:1024],
            branch=branch_name[:255],
            success=True,
            metadata={
                "run_id": str(run_id),
                "sequence": index,
                "workspace_id": workspace_id if checks else "",
                "risk_flags": change.get("risk_flags") or [],
                "commit_sha": result.get("commit_sha"),
                "content_sha": result.get("content_sha"),
            },
        )
        applied.append(
            {
                "path": change["path"],
                "operation": change["operation"],
                "risk_flags": change.get("risk_flags") or [],
                "commit_sha": result.get("commit_sha"),
                "content_sha": result.get("content_sha"),
            }
        )
    _persist_run_execution_state(
        run_id,
        phase="changes_written",
        workspace_id=workspace_id if checks else "",
        working_branch=branch_name,
    )
    return {
        "branch": branch_name,
        "changes": applied,
        "sandbox": sandbox_result,
        "workspace_id": workspace_id if checks else None,
    }
