from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .dev_context import build_repository_context
from .models import AgentApproval, AgentRun, AgentTeam
from .readiness import agent_readiness
from .serializers import AgentRunSerializer
from .team_readiness import team_readiness


class SafeAgentApprovalDecisionView(APIView):
    @transaction.atomic
    def post(self, request, run_id, approval_id):
        run = get_object_or_404(
            AgentRun.objects.select_for_update().select_related("agent", "team", "team__director", "project"),
            id=run_id,
            owner=request.user,
        )
        approval = AgentApproval.objects.select_for_update().filter(id=approval_id, run=run).first()
        if approval is None:
            raise ValidationError({"approval": "Запрос подтверждения не найден"})
        if approval.status != AgentApproval.Status.PENDING:
            return Response(AgentRunSerializer(run).data)

        decision = str(request.data.get("decision") or "").strip()
        if decision not in {AgentApproval.Status.APPROVED, AgentApproval.Status.REJECTED}:
            raise ValidationError({"decision": "Используйте approved или rejected"})

        # A human decision may arrive hours later. Revalidate dependencies before
        # changing the approval state so stale permissions cannot resume work.
        if decision == AgentApproval.Status.APPROVED:
            if run.agent_id:
                readiness = agent_readiness(run.agent)
                if not readiness["ready"]:
                    raise ValidationError(
                        {"detail": "Перед продолжением восстановите готовность сотрудника: " + "; ".join(readiness["blockers"])}
                    )
            elif run.team_id:
                readiness = team_readiness(run.team)
                if not readiness["ready"]:
                    raise ValidationError(
                        {"detail": "Перед продолжением восстановите готовность команды: " + "; ".join(readiness["blockers"])}
                    )
                payload = approval.action_payload or {}
                is_dev_write = (
                    run.team.kind == AgentTeam.Kind.DEVELOPMENT
                    and str(payload.get("kind") or "") == "github_changes"
                )
                if is_dev_write:
                    try:
                        build_repository_context(run.project)
                    except Exception as exc:
                        raise ValidationError(
                            {"detail": f"GitHub repository недоступен. Изменения не подтверждены: {exc}"}
                        ) from exc

        now = timezone.now()
        approval.status = decision
        approval.decided_by = request.user
        approval.decided_at = now
        approval.save(update_fields=["status", "decided_by", "decided_at"])

        if decision == AgentApproval.Status.REJECTED:
            run.state = AgentRun.State.CANCELED
            run.finished_at = now
            run.save(update_fields=["state", "finished_at", "updated_at"])
            return Response(AgentRunSerializer(run).data)

        if run.state == AgentRun.State.WAITING_APPROVAL:
            run.state = AgentRun.State.QUEUED
            run.finished_at = None
            run.save(update_fields=["state", "finished_at", "updated_at"])
            from .tasks import enqueue_agent_run

            transaction.on_commit(lambda current_run_id=str(run.id): enqueue_agent_run(current_run_id))

        return Response(AgentRunSerializer(run).data)
