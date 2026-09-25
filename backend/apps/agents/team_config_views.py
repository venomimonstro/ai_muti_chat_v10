from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Agent, AgentTeam, AgentTeamMember
from .serializers import AgentTeamSerializer


class AgentTeamMemberDetailView(APIView):
    @transaction.atomic
    def patch(self, request, team_id, member_id):
        team = get_object_or_404(AgentTeam.objects.select_for_update(), id=team_id, owner=request.user)
        member = get_object_or_404(
            AgentTeamMember.objects.select_for_update().select_related("agent"),
            id=member_id,
            team=team,
        )
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
        member.full_clean()
        member.save()
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
