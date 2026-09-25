from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent, AgentRun, AgentTeam
from .readiness import require_agent_ready
from .run_views import ACTIVE_RUN_STATES, create_single_agent_run
from .serializers import AgentRunSerializer
from .team_readiness import team_readiness


class AgentRunRepeatView(APIView):
    """Create a fresh run from a terminal run without mutating accounting/history."""

    @transaction.atomic
    def post(self, request, run_id):
        previous = get_object_or_404(
            AgentRun.objects.select_related("agent", "team", "project"),
            id=run_id,
            owner=request.user,
        )
        if previous.state in ACTIVE_RUN_STATES:
            return Response(AgentRunSerializer(previous).data, status=status.HTTP_200_OK)

        objective = str(request.data.get("objective") or previous.objective or "").strip()
        if not objective:
            raise ValidationError({"objective": "Укажите задачу повторного запуска"})
        input_payload = {
            "trigger": "repeat",
            "previous_run_id": str(previous.id),
        }

        if previous.agent_id:
            agent = Agent.objects.select_for_update().get(pk=previous.agent_id, owner=request.user)
            existing = (
                AgentRun.objects.filter(owner=request.user, agent=agent, state__in=ACTIVE_RUN_STATES)
                .order_by("-created_at")
                .first()
            )
            if existing is not None:
                return Response(AgentRunSerializer(existing).data, status=status.HTTP_200_OK)
            if agent.status != Agent.Status.ACTIVE:
                raise ValidationError({"detail": "Сотрудник приостановлен. Сначала активируйте его."})
            try:
                require_agent_ready(agent)
            except Exception as exc:
                raise ValidationError({"detail": f"Сотрудник не готов к повторному запуску: {exc}"}) from exc
            run = create_single_agent_run(
                owner=request.user,
                agent=agent,
                objective=objective,
                input_payload=input_payload,
            )
            return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)

        team = (
            AgentTeam.objects.select_for_update()
            .select_related("director", "project")
            .prefetch_related("members__agent")
            .get(pk=previous.team_id, owner=request.user)
        )
        existing = (
            AgentRun.objects.filter(owner=request.user, team=team, state__in=ACTIVE_RUN_STATES)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return Response(AgentRunSerializer(existing).data, status=status.HTTP_200_OK)
        readiness = team_readiness(team)
        if not readiness["ready"]:
            raise ValidationError({"detail": "Команда не готова к повторному запуску: " + "; ".join(readiness["blockers"])})

        run = AgentRun.objects.create(
            owner=request.user,
            team=team,
            project=team.project,
            objective=objective,
            input_payload=input_payload,
            state=AgentRun.State.QUEUED,
        )
        from .tasks import enqueue_agent_run

        transaction.on_commit(lambda new_run_id=str(run.id): enqueue_agent_run(new_run_id))
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)
