from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AgentApproval, AgentRun
from .readiness import agent_readiness
from .serializers import AgentRunSerializer


class SafeAgentApprovalDecisionView(APIView):
    @transaction.atomic
    def post(self, request, run_id, approval_id):
        run = get_object_or_404(
            AgentRun.objects.select_for_update().select_related("agent", "team"),
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

        # A human decision may arrive hours later. Revalidate a single agent
        # before changing the approval state so a revoked model/tool/WordPress
        # connection cannot resume a paid workflow with stale assumptions.
        if decision == AgentApproval.Status.APPROVED and run.agent_id:
            readiness = agent_readiness(run.agent)
            if not readiness["ready"]:
                raise ValidationError(
                    {"detail": "Перед продолжением восстановите готовность сотрудника: " + "; ".join(readiness["blockers"])}
                )

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
