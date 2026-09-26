from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .limits import ensure_owner_run_capacity
from .models import AgentRun, AgentTeam
from .serializers import AgentRunSerializer
from .team_readiness import team_readiness


ACTIVE_RUN_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}


class TeamReadinessView(APIView):
    def get(self, request, team_id):
        team = get_object_or_404(
            AgentTeam.objects.filter(owner=request.user)
            .select_related("director", "project", "project__github_repository__installation")
            .prefetch_related("members__agent"),
            id=team_id,
        )
        return Response(team_readiness(team))


class SafeTeamRunView(APIView):
    @transaction.atomic
    def post(self, request, team_id):
        scoped = get_object_or_404(AgentTeam, id=team_id, owner=request.user)
        team = (
            AgentTeam.objects.select_for_update()
            .select_related("director", "project")
            .prefetch_related("members__agent")
            .get(pk=scoped.pk)
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
            raise ValidationError({"detail": "Команда не готова к запуску: " + "; ".join(readiness["blockers"])})

        objective = str(request.data.get("objective") or team.objective or "").strip()
        if not objective:
            raise ValidationError({"objective": "Укажите задачу команды"})
        try:
            ensure_owner_run_capacity(request.user)
        except DjangoValidationError as exc:
            raise ValidationError({"detail": "; ".join(exc.messages)}) from exc

        run = AgentRun.objects.create(
            owner=request.user,
            team=team,
            project=team.project,
            objective=objective,
            input_payload=request.data.get("input") or {},
            state=AgentRun.State.QUEUED,
        )
        from .tasks import enqueue_agent_run

        transaction.on_commit(lambda run_id=str(run.id): enqueue_agent_run(run_id))
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)
