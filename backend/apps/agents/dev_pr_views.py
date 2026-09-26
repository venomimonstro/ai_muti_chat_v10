from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.github_integration.models import GitHubOperationLog
from apps.github_integration.pull_requests import create_pull_request

from .models import AgentRun, AgentTeam


class DevRunPullRequestView(APIView):
    @transaction.atomic
    def post(self, request, run_id):
        run = get_object_or_404(
            AgentRun.objects.select_for_update().select_related(
                "team", "project__github_repository__installation"
            ),
            id=run_id,
            owner=request.user,
        )
        if not run.team_id or run.team.kind != AgentTeam.Kind.DEVELOPMENT:
            raise ValidationError({"detail": "Pull Request доступен только для Dev Studio"})
        if run.state != AgentRun.State.COMPLETED:
            raise ValidationError({"detail": "Сначала дождитесь успешного завершения QA и Final Review"})
        if not run.project_id:
            raise ValidationError({"detail": "Dev-run не привязан к проекту"})

        output = dict(run.output_payload or {})
        existing = output.get("pull_request")
        if isinstance(existing, dict) and existing.get("number") and existing.get("html_url"):
            return Response({"pull_request": existing, "created": False})

        binding = run.project.github_repository
        if not binding.installation.active:
            raise ValidationError({"detail": "GitHub-подключение проекта отключено"})
        if not binding.write_enabled:
            raise ValidationError({"detail": "Для проекта не разрешена запись в GitHub"})

        branch = str(output.get("working_branch") or "").strip()
        if not branch:
            raise ValidationError({"detail": "В этом запуске нет рабочей ветки с изменениями"})
        if branch == str(binding.default_branch or "").strip():
            raise ValidationError({"detail": "Dev Studio не создаёт Pull Request из default branch"})
        if not branch.startswith("ai-workspace/run-"):
            raise ValidationError({"detail": "Рабочая ветка не принадлежит этому Dev Studio run"})
        expected_prefix = f"ai-workspace/run-{str(run.id).replace('-', '')[:12]}"
        if branch != expected_prefix:
            raise ValidationError({"detail": "Рабочая ветка не соответствует текущему Dev Studio run"})

        summary = str(output.get("text") or "").strip()
        body = (
            "Изменения подготовлены Dev Studio после sandbox-проверки, QA/Security и Final Review.\n\n"
            f"Задача:\n{run.objective[:6000]}\n\n"
            f"Итог review:\n{summary[:12000]}\n\n"
            f"AI Workspace run: {run.id}"
        )
        pull = create_pull_request(
            binding,
            head=branch,
            base=binding.default_branch,
            title=f"Dev Studio: {run.objective.strip()[:190] or str(run.id)[:8]}",
            body=body,
        )
        output["pull_request"] = pull
        run.output_payload = output
        run.save(update_fields=["output_payload", "updated_at"])
        GitHubOperationLog.objects.create(
            actor=request.user,
            binding=binding,
            action="agent_create_pull_request",
            path="",
            branch=branch[:255],
            success=True,
            metadata={
                "run_id": str(run.id),
                "pull_request_number": pull.get("number"),
                "pull_request_url": pull.get("html_url"),
                "existing": bool(pull.get("existing")),
            },
        )
        return Response({"pull_request": pull, "created": not bool(pull.get("existing"))})
