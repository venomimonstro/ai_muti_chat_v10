from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent, AgentApproval, AgentRun
from .readiness import require_agent_ready
from .serializers import AgentRunSerializer


ACTIVE_RUN_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}


def enqueue_agent_run(run):
    from .tasks import enqueue_agent_run as safe_enqueue_agent_run

    transaction.on_commit(lambda run_id=str(run.id): safe_enqueue_agent_run(run_id))


def create_single_agent_run(*, owner, agent, objective, input_payload=None):
    state = (
        AgentRun.State.WAITING_APPROVAL
        if agent.autonomy == Agent.Autonomy.CONTROLLED
        else AgentRun.State.QUEUED
    )
    run = AgentRun.objects.create(
        owner=owner,
        agent=agent,
        project=agent.project,
        objective=objective,
        input_payload=input_payload or {},
        state=state,
        cost_reserved_rub=Decimal("0"),
    )
    if state == AgentRun.State.WAITING_APPROVAL:
        AgentApproval.objects.create(
            run=run,
            requested_by_agent=agent,
            title="Разрешить запуск контролируемого агента",
            description=(
                "Этот агент работает в контролируемом режиме. До подтверждения система не обращается к LLM "
                "и не вызывает внешние или платные инструменты."
            ),
            action_payload={"kind": "controlled_run_start"},
        )
    else:
        enqueue_agent_run(run)
    return run


class SafeAgentRunView(APIView):
    @transaction.atomic
    def post(self, request, agent_id):
        scoped = get_object_or_404(Agent, id=agent_id, owner=request.user)
        agent = Agent.objects.select_for_update().get(pk=scoped.pk)
        if agent.status != Agent.Status.ACTIVE:
            raise ValidationError({"detail": "Сначала активируйте агента"})
        existing = (
            AgentRun.objects.filter(owner=request.user, agent=agent, state__in=ACTIVE_RUN_STATES)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return Response(AgentRunSerializer(existing).data, status=status.HTTP_200_OK)
        try:
            require_agent_ready(agent)
        except Exception as exc:
            raise ValidationError({"detail": f"Сотрудник не готов к запуску: {exc}"}) from exc
        objective = str(request.data.get("objective") or agent.objective).strip()
        if not objective:
            raise ValidationError({"objective": "Укажите задачу запуска"})
        run = create_single_agent_run(
            owner=request.user,
            agent=agent,
            objective=objective,
            input_payload=request.data.get("input") or {},
        )
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)
