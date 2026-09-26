from django.core.exceptions import ValidationError

from apps.github_integration.branching import create_repository_branch
from apps.github_integration.models import GitHubOperationLog
from apps.github_integration.mutations import create_repository_file
from apps.github_integration.services import write_repository_file

from .sandbox_client import run_sandbox, sandbox_enabled


MAX_EXECUTION_CHANGES = 12


def enrich_changes_with_snapshot(changes, repository_context):
    visible = {
        str(item.get("path") or ""): item
        for item in (repository_context.get("files") or [])
        if item.get("path")
    }
    result = []
    for change in changes[:MAX_EXECUTION_CHANGES]:
        item = dict(change)
        if item["operation"] == "update":
            source = visible.get(item["path"])
            if not source:
                raise ValidationError(
                    f"Нельзя обновить {item['path']}: файл не был прочитан Developer перед предложением изменения"
                )
            expected_sha = str(source.get("sha") or "").strip()
            if not expected_sha:
                raise ValidationError(f"Для {item['path']} отсутствует исходный SHA")
            item["expected_sha"] = expected_sha
        result.append(item)
    return result


def _sandbox_command(changes):
    paths = [str(item["path"]).lower() for item in changes]
    if any(path.endswith(".py") for path in paths):
        return "python-compile"
    return None


def validate_changes_in_sandbox(changes):
    command = _sandbox_command(changes)
    if command is None:
        return {
            "ok": True,
            "command": "text-validation-only",
            "returncode": 0,
            "output": "Для предложенных типов файлов нет исполняемой sandbox-проверки; выполнена структурная валидация.",
        }
    if not sandbox_enabled():
        raise ValidationError("Sandbox не настроен; запись кода заблокирована")
    files = [{"path": item["path"], "content": item["content"]} for item in changes]
    result = run_sandbox(command=command, files=files)
    if not result.get("ok"):
        raise ValidationError(
            f"Sandbox отклонил изменения ({result.get('command')}): {str(result.get('output') or result.get('error') or '')[-4000:]}"
        )
    return result


def _default_should_cancel(run_id):
    from .models import AgentRun

    state = AgentRun.objects.filter(pk=run_id).values_list("state", flat=True).first()
    return state == AgentRun.State.CANCELED


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
    sandbox_result = validate_changes_in_sandbox(changes)
    if cancel_check():
        raise ValidationError("Dev Studio остановлен после sandbox и до создания рабочей ветки")

    branch_name = f"ai-workspace/run-{str(run_id).replace('-', '')[:12]}"
    create_repository_branch(binding, branch_name, from_ref=binding.default_branch)
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
                "commit_sha": result.get("commit_sha"),
                "content_sha": result.get("content_sha"),
            },
        )
        applied.append(
            {
                "path": change["path"],
                "operation": change["operation"],
                "commit_sha": result.get("commit_sha"),
                "content_sha": result.get("content_sha"),
            }
        )
    return {"branch": branch_name, "changes": applied, "sandbox": sandbox_result}
