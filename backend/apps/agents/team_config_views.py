from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent, AgentTeam, AgentTeamMember
from .serializers import AgentTeamSerializer
from .versioning import create_agent_version


AGENT_BUDGET_FIELDS = (
    "max_cost_rub_per_run",
    "max_cost_rub_per_day",
    "max_cost_rub_per_month",
)


class AgentTeamMemberDetailView(APIView):
    @transaction.atomic
    def patch(self, request, team_id, member_id):
        team = get_object_or_404(AgentTeam.objects.select_for_update(), id=team_id, owner=request.user)
        member = get_object_or_404(
            AgentTeamMember.objects.select_for_update().select_related("agent"),
            id=member_id,
            team=team,
        )
        agent = Agent.objects.select_for_update().get(pk=member.agent_id, owner=request.user)

        role = request.data.get("role")
        priority = request.data.get("priority")
        enabled = request.data.get("enabled")
        can_delegate = request.data.get("can_delegate")
        if role is not None:
            role = str(role).strip()
            if not role or len(role) > 160:
                raise ValidationError({"role": "Укажите корректную роль"})
            member.role = role
        if priority is not None:
            try:
                priority = int(priority)
            except (TypeError, ValueError):
                raise ValidationError({"priority": "Приоритет должен быть числом"})
            if priority < 1 or priority > 10000:
                raise ValidationError({"priority": "Приоритет должен быть от 1 до 10000"})
            member.priority = priority
        if enabled is not None:
            member.enabled = bool(enabled)
        if can_delegate is not None:
            member.can_delegate = bool(can_delegate)
        if member.agent_id == team.director_id and not member.enabled:
            raise ValidationError({"enabled": "Руководителя команды нельзя отключить"})

        agent_changed = False
        if "system_level" in request.data:
            level = str(request.data.get("system_level") or "").strip()
            if level not in {"economy", "balanced", "maximum"}:
                raise ValidationError({"system_level": "Выберите System Lite, Pro или Max"})
            agent.system_level = level
            agent_changed = True

        for field in AGENT_BUDGET_FIELDS:
            if field not in request.data:
                continue
            try:
                value = Decimal(str(request.data.get(field)))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError({field: "Укажите корректную сумму"})
            setattr(agent, field, value)
            agent_changed = True

        try:
            member.full_clean()
            if agent_changed:
                agent.full_clean()
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError(detail) from exc

        member.save()
        if agent_changed:
            create_agent_version(agent, request.user)
            agent.save(
                update_fields=[
                    "system_level",
                    "max_cost_rub_per_run",
                    "max_cost_rub_per_day",
                    "max_cost_rub_per_month",
                    "updated_at",
                ]
            )
        team.refresh_from_db()
        return Response(AgentTeamSerializer(team, context={"request": request}).data)

    @transaction.atomic
    def delete(self, request, team_id, member_id):
        team = get_object_or_404(AgentTeam.objects.select_for_update(), id=team_id, owner=request.user)
        member = get_object_or_404(AgentTeamMember.objects.select_for_update(), id=member_id, team=team)
        if member.agent_id == team.director_id:
            raise ValidationError({"detail": "Сначала назначьте другого руководителя команды"})
        member.delete()
        team.refresh_from_db()
        return Response(AgentTeamSerializer(team, context={"request": request}).data)


class AgentTeamDirectorView(APIView):
    @transaction.atomic
    def post(self, request, team_id):
        team = get_object_or_404(AgentTeam.objects.select_for_update(), id=team_id, owner=request.user)
        agent = get_object_or_404(Agent, id=request.data.get("agent"), owner=request.user)
        member = AgentTeamMember.objects.filter(team=team, agent=agent, enabled=True).first()
        if member is None:
            raise ValidationError({"agent": "Руководитель должен быть активным участником этой команды"})
        team.director = agent
        team.full_clean()
        team.save(update_fields=["director", "updated_at"])
        if not member.can_delegate:
            member.can_delegate = True
            member.save(update_fields=["can_delegate"])
        team.refresh_from_db()
        return Response(AgentTeamSerializer(team, context={"request": request}).data)
