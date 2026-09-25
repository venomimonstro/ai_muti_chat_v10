from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent, AgentApproval, AgentRun, AgentTeam
from .schedule_models import AgentSchedule


ACTIVE_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.REVIEWING,
}


class AgentOperationsSummaryView(APIView):
    """Small owner-scoped operational snapshot for Agent Studio dashboards."""

    def get(self, request):
        now = timezone.now()
        since = now - timedelta(days=30)
        runs = AgentRun.objects.filter(owner=request.user)
        recent = runs.filter(created_at__gte=since)

        state_counts = {
            row["state"]: row["total"]
            for row in recent.values("state").annotate(total=Count("id"))
        }
        recent_cost = recent.aggregate(total=Sum("cost_actual_rub"))["total"] or Decimal("0")
        autonomous_recent = recent.filter(
            input_payload__trigger__in=["schedule", "schedule_run_now"]
        )
        autonomous_cost = autonomous_recent.aggregate(total=Sum("cost_actual_rub"))["total"] or Decimal("0")

        return Response(
            {
                "generated_at": now,
                "window_days": 30,
                "agents": {
                    "total": Agent.objects.filter(owner=request.user).count(),
                    "active": Agent.objects.filter(owner=request.user, status=Agent.Status.ACTIVE).count(),
                },
                "teams": {
                    "total": AgentTeam.objects.filter(owner=request.user).count(),
                    "active": AgentTeam.objects.filter(owner=request.user, active=True).count(),
                },
                "schedules": {
                    "total": AgentSchedule.objects.filter(owner=request.user).count(),
                    "enabled": AgentSchedule.objects.filter(owner=request.user, enabled=True).count(),
                },
                "runs": {
                    "active": runs.filter(state__in=ACTIVE_STATES).count(),
                    "waiting_approval": runs.filter(state=AgentRun.State.WAITING_APPROVAL).count(),
                    "completed_30d": state_counts.get(AgentRun.State.COMPLETED, 0),
                    "failed_30d": state_counts.get(AgentRun.State.FAILED, 0),
                    "budget_exceeded_30d": state_counts.get(AgentRun.State.BUDGET_EXCEEDED, 0),
                    "canceled_30d": state_counts.get(AgentRun.State.CANCELED, 0),
                    "total_30d": recent.count(),
                },
                "approvals": {
                    "pending": AgentApproval.objects.filter(
                        run__owner=request.user,
                        status=AgentApproval.Status.PENDING,
                    ).count(),
                },
                "cost": {
                    "actual_rub_30d": str(recent_cost.quantize(Decimal("0.0001"))),
                    "autonomous_rub_30d": str(autonomous_cost.quantize(Decimal("0.0001"))),
                },
            }
        )
