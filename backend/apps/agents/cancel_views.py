from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AgentApproval, AgentRun
from .serializers import AgentRunSerializer


TERMINAL_STATES = {
    AgentRun.State.COMPLETED,
    AgentRun.State.FAILED,
    AgentRun.State.CANCELED,
    AgentRun.State.BUDGET_EXCEEDED,
}


class SafeAgentRunCancelView(APIView):
    """Cancel one owned run without leaving dangling approvals behind."""

    @transaction.atomic
    def post(self, request, run_id):
        run = get_object_or_404(
            AgentRun.objects.select_for_update()
            .select_related("agent", "team", "project")
            .prefetch_related("steps__agent", "approvals"),
            id=run_id,
            owner=request.user,
        )
        if run.state in TERMINAL_STATES:
            return Response(AgentRunSerializer(run).data)

        now = timezone.now()
        pending = AgentApproval.objects.select_for_update().filter(
            run=run,
            status=AgentApproval.Status.PENDING,
        )
        # A user cancel is an explicit human rejection of every still-pending
        # protected action. post_save signals keep any approval step in sync.
        for approval in pending:
            approval.status = AgentApproval.Status.REJECTED
            approval.decided_by = request.user
            approval.decided_at = now
            approval.save(update_fields=["status", "decided_by", "decided_at"])

        run.state = AgentRun.State.CANCELED
        run.finished_at = now
        run.error_code = "user_canceled"
        run.error_message = "Запуск остановлен пользователем. Незавершённые внешние действия не выполняются."
        run.save(
            update_fields=[
                "state",
                "finished_at",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )
        return Response(AgentRunSerializer(run).data)
