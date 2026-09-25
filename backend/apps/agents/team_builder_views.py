from decimal import Decimal

from django.db import transaction
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.projects.models import Project

from .models import Agent, AgentTeam, AgentTeamMember
from .serializers import AgentTeamSerializer
from .team_builder import team_draft


class AgentTeamBootstrapView(APIView):
    @transaction.atomic
    def post(self, request):
        description = str(request.data.get("description") or "").strip()
        if len(description) < 12:
            raise ValidationError({"description": "Опишите команду и её задачи чуть подробнее"})

        project = None
        project_id = request.data.get("project")
        if project_id:
            project = Project.objects.filter(id=project_id, owner=request.user, archived_at__isnull=True).first()
            if project is None:
                raise ValidationError({"project": "Проект недоступен"})

        draft = team_draft(description)
        if draft["kind"] == "development":
            raise ValidationError(
                {
                    "description": (
                        "Для команды разработки используйте Dev Studio: там обязательны GitHub, "
                        "рабочая ветка, sandbox и отдельный контур подтверждений."
                    )
                }
            )

        created = []
        for index, spec in enumerate(draft["members"]):
            is_director = index == 0
            agent = Agent.objects.create(
                owner=request.user,
                project=project,
                name=spec["name"],
                role=spec["role"],
                objective=(
                    f"Работать в рамках общей цели команды: {description}. "
                    f"Зона ответственности: {spec['role']}."
                ),
                autonomy=Agent.Autonomy.SEMI_AUTONOMOUS if not is_director else Agent.Autonomy.CONTROLLED,
                status=Agent.Status.ACTIVE,
                system_level=spec["level"],
                tool_policy=spec["tools"],
                graph=spec["graph"],
                max_cost_rub_per_run=Decimal("20" if not is_director else "30"),
                max_steps=60,
                max_tool_calls=80,
                max_handoffs=30,
                max_runtime_seconds=3600,
            )
            created.append(agent)

        director = created[0]
        team = AgentTeam.objects.create(
            owner=request.user,
            project=project,
            name=str(request.data.get("name") or draft["name"]).strip()[:160],
            objective=description,
            kind=draft["kind"],
            director=director,
            max_cost_rub_per_run=Decimal(str(request.data.get("max_cost_rub_per_run") or "75")),
            max_handoffs=40,
        )
        for index, agent in enumerate(created):
            AgentTeamMember.objects.create(
                team=team,
                agent=agent,
                role=agent.role or agent.name,
                priority=(index + 1) * 10,
                can_delegate=index == 0,
                enabled=True,
            )

        return Response(AgentTeamSerializer(team, context={"request": request}).data, status=201)
