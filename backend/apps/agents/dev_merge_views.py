from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.github_integration.models import GitHubOperationLog
from apps.github_integration.pull_requests import get_pull_request, merge_pull_request

from .models import AgentRun, AgentTeam


class DevRunMergePullRequestView(APIView):
    """Manually merge the exact Pull Request produced by a completed Dev run.

    Merge is never autonomous: the caller must explicitly confirm the action.
    The stored PR head/base/SHA are treated as the immutable approval target.
    """

    @transaction.atomic
    def post(self, request, run_id):
        if request.data.get("confirm_merge") is not True:
            raise ValidationError({"confirm_merge": "Явное подтверждение merge обязательно"})

        merge_method = str(request.data.get("merge_method") or "squash").strip().lower()
        if merge_method not in {"merge", "squash", "rebase"}:
            raise ValidationError({"merge_method": "Используйте merge, squash или rebase"})

        run = get_object_or_404(
            AgentRun.objects.select_for_update().select_related(
                "team", "project__github_repository__installation"
            ),
            id=run_id,
            owner=request.user,
        )
        if not run.team_id or run.team.kind != AgentTeam.Kind.DEVELOPMENT:
            raise ValidationError({"detail": "Merge доступен только для Dev Studio"})
        if run.state != AgentRun.State.COMPLETED:
            raise ValidationError({"detail": "Merge разрешён только для успешно завершённого Dev-run"})
        if not run.project_id:
            raise ValidationError({"detail": "Dev-run не привязан к проекту"})

        output = dict(run.output_payload or {})
        pull = output.get("pull_request")
        if not isinstance(pull, dict):
            raise ValidationError({"detail": "Сначала создайте Pull Request для этого Dev-run"})

        if pull.get("merged") is True and pull.get("merge_sha"):
            return Response({"pull_request": pull, "merged": True, "created": False})

        number = int(pull.get("number") or 0)
        expected_head = str(pull.get("head") or "").strip()
        expected_head_sha = str(pull.get("head_sha") or "").strip()
        expected_base = str(pull.get("base") or "").strip()
        if number <= 0 or not expected_head or not expected_head_sha:
            raise ValidationError({"detail": "В сохранённом Pull Request отсутствуют обязательные GitHub данные"})

        binding = run.project.github_repository
        if not binding.installation.active:
            raise ValidationError({"detail": "GitHub-подключение проекта отключено"})
        if not binding.write_enabled:
            raise ValidationError({"detail": "Для проекта не разрешена запись в GitHub"})
        contents_permission = str((binding.installation.permissions or {}).get("contents") or "").lower()
        if contents_permission not in {"write", "admin"}:
            raise ValidationError({"detail": "GitHub App больше не имеет contents:write"})
        if expected_base != str(binding.default_branch or "").strip():
            raise ValidationError({"detail": "Default branch проекта изменилась после создания Pull Request"})

        expected_branch = f"ai-workspace/run-{str(run.id).replace('-', '')[:12]}"
        if expected_head != expected_branch:
            raise ValidationError({"detail": "Pull Request не соответствует рабочей ветке этого Dev-run"})

        # Re-read GitHub before merge. This also recovers the crash window where
        # GitHub completed a previous merge but our DB update did not commit.
        current = get_pull_request(binding, number)
        current_head = str(((current.get("head") or {}).get("ref")) or "")
        current_head_sha = str(((current.get("head") or {}).get("sha")) or "")
        current_base = str(((current.get("base") or {}).get("ref")) or "")
        github_merged = bool(current.get("merged") or current.get("merged_at"))
        if current_head != expected_head or current_base != expected_base:
            raise ValidationError({"detail": "Pull Request изменился после подтверждённого Dev-run; merge заблокирован"})
        if current_head_sha != expected_head_sha:
            raise ValidationError({"detail": "В Pull Request появились новые commits; повторная проверка Dev Studio обязательна"})

        if github_merged:
            merge_sha = str(current.get("merge_commit_sha") or expected_head_sha)
            result = {"merged": True, "sha": merge_sha, "message": "Merge уже был выполнен в GitHub", "number": number}
            recovered = True
        else:
            result = merge_pull_request(
                binding,
                number=number,
                expected_head=expected_head,
                expected_head_sha=expected_head_sha,
                merge_method=merge_method,
            )
            recovered = False

        pull = {
            **pull,
            "state": "merged",
            "merged": True,
            "merge_sha": str(result.get("sha") or ""),
            "merge_method": merge_method,
        }
        output["pull_request"] = pull
        run.output_payload = output
        run.save(update_fields=["output_payload", "updated_at"])

        GitHubOperationLog.objects.create(
            actor=request.user,
            binding=binding,
            action="agent_merge_pull_request",
            path="",
            branch=expected_head[:255],
            success=True,
            metadata={
                "run_id": str(run.id),
                "pull_request_number": number,
                "merge_sha": pull["merge_sha"],
                "merge_method": merge_method,
                "recovered_existing_merge": recovered,
            },
        )
        return Response({"pull_request": pull, "merged": True, "created": not recovered})
