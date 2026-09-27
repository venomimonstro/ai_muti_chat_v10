import difflib

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.github_integration.services import read_repository_file

from .models import AgentApproval, AgentRun, AgentTeam


MAX_DIFF_CHARS = 120000


def _dev_run(request, run_id):
    run = get_object_or_404(
        AgentRun.objects.select_related("team", "project__github_repository__installation"),
        id=run_id,
        owner=request.user,
    )
    if not run.team_id or run.team.kind != AgentTeam.Kind.DEVELOPMENT or not run.project_id:
        raise ValidationError({"detail": "Операция доступна только для Dev Studio run"})
    return run


def _changes_approval(run):
    approval = (
        AgentApproval.objects.filter(run=run, action_payload__kind="github_changes")
        .order_by("-created_at")
        .first()
    )
    if approval is None:
        raise ValidationError({"detail": "Для этого run нет предложения изменений"})
    return approval


class DevRunChangesPreviewView(APIView):
    """Render exact user-reviewable diffs before approving GitHub writes."""

    def get(self, request, run_id):
        run = _dev_run(request, run_id)
        approval = _changes_approval(run)
        payload = approval.action_payload or {}
        base_branch = str(payload.get("base_branch") or run.project.github_repository.default_branch)
        changes = list(payload.get("changes") or [])
        binding = run.project.github_repository
        rendered = []
        total_chars = 0

        for change in changes:
            path = str(change.get("path") or "")
            operation = str(change.get("operation") or "")
            proposed = str(change.get("content") or "")
            if operation == "update":
                current = read_repository_file(binding, path, ref=base_branch)
                expected_sha = str(change.get("expected_sha") or "")
                if expected_sha and str(current.get("sha") or "") != expected_sha:
                    raise ValidationError(
                        {"detail": f"Файл {path} изменился после анализа. Нужно заново запустить Dev Studio."}
                    )
                original = str(current.get("content") or "")
            elif operation == "create":
                original = ""
            else:
                raise ValidationError({"detail": f"Неподдерживаемая операция для {path}"})

            diff = "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    proposed.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                    n=3,
                )
            )
            total_chars += len(diff)
            if total_chars > MAX_DIFF_CHARS:
                raise ValidationError({"detail": "Diff слишком большой для безопасного просмотра одним подтверждением"})
            rendered.append(
                {
                    "path": path,
                    "operation": operation,
                    "reason": str(change.get("reason") or ""),
                    "expected_sha": str(change.get("expected_sha") or ""),
                    "before_chars": len(original),
                    "after_chars": len(proposed),
                    "diff": diff,
                }
            )

        return Response(
            {
                "run_id": str(run.id),
                "approval_id": str(approval.id),
                "approval_status": approval.status,
                "repository": binding.full_name,
                "base_branch": base_branch,
                "default_branch_unchanged": True,
                "changes": rendered,
            }
        )


class DevRunAbandonBranchView(APIView):
    """Mark an isolated Dev branch as abandoned without touching default branch."""

    def post(self, request, run_id):
        if request.data.get("confirm_abandon") is not True:
            raise ValidationError({"confirm_abandon": "Явное подтверждение обязательно"})
        run = _dev_run(request, run_id)
        output = dict(run.output_payload or {})
        pull = output.get("pull_request")
        if isinstance(pull, dict) and pull.get("merged") is True:
            raise ValidationError({"detail": "Ветка уже была merged; abandon больше не является rollback"})
        branch = str(output.get("working_branch") or (run.input_payload or {}).get("working_branch") or "").strip()
        expected = f"ai-workspace/run-{str(run.id).replace('-', '')[:12]}"
        if not branch:
            raise ValidationError({"detail": "У run нет созданной рабочей ветки"})
        if branch != expected:
            raise ValidationError({"detail": "Рабочая ветка не соответствует этому Dev Studio run"})

        output["working_branch"] = branch
        output["branch_disposition"] = "abandoned"
        output["recovery_note"] = (
            "Default branch не изменялась. Изолированная ветка сохранена для аудита/ручного удаления в GitHub."
        )
        run.output_payload = output
        run.save(update_fields=["output_payload", "updated_at"])
        return Response(
            {
                "run_id": str(run.id),
                "branch": branch,
                "disposition": "abandoned",
                "default_branch_unchanged": True,
                "remote_branch_deleted": False,
            }
        )
