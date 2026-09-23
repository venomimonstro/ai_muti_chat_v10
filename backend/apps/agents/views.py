from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import Agent, AgentApproval, AgentRun, AgentTeam, AgentTeamMember
from .serializers import AgentRunSerializer, AgentSerializer, AgentTeamSerializer


AGENT_TEMPLATES = [
    {
        "slug": "smm-specialist",
        "name": "SMM-специалист",
        "role": "SMM specialist",
        "objective": "Планировать и создавать контент, проверять факты, готовить материалы к публикации и анализировать результат.",
        "autonomy": Agent.Autonomy.SEMI_AUTONOMOUS,
        "system_level": "balanced",
        "tool_policy": {"web": True, "files": True, "images": True, "publish": "approval"},
    },
    {
        "slug": "seo-copywriter",
        "name": "SEO-копирайтер",
        "role": "SEO copywriter",
        "objective": "Исследовать тему, собирать факты, писать SEO-материалы, проверять дубли и готовить публикацию.",
        "autonomy": Agent.Autonomy.SEMI_AUTONOMOUS,
        "system_level": "balanced",
        "tool_policy": {"web": True, "files": True, "publish": "approval"},
    },
    {
        "slug": "software-engineer",
        "name": "Разработчик",
        "role": "Software Engineer",
        "objective": "Анализировать код, вносить изменения, запускать проверки и подготавливать безопасные изменения проекта.",
        "autonomy": Agent.Autonomy.CONTROLLED,
        "system_level": "balanced",
        "tool_policy": {"github": True, "files": True, "shell": "sandbox", "write_code": True, "merge": "approval"},
    },
    {
        "slug": "qa-engineer",
        "name": "QA-инженер",
        "role": "QA Engineer",
        "objective": "Проверять требования, запускать тесты, искать регрессии и оформлять воспроизводимые дефекты.",
        "autonomy": Agent.Autonomy.CONTROLLED,
        "system_level": "balanced",
        "tool_policy": {"github": True, "files": True, "shell": "sandbox", "write_code": False},
    },
]


class AgentViewSet(viewsets.ModelViewSet):
    serializer_class = AgentSerializer

    def get_queryset(self):
        return Agent.objects.filter(owner=self.request.user).select_related("project")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=False, methods=["get"], url_path="templates")
    def templates(self, request):
        return Response(AGENT_TEMPLATES)

    @action(detail=False, methods=["post"], url_path="from-template")
    @transaction.atomic
    def from_template(self, request):
        slug = str(request.data.get("template") or "").strip()
        template = next((item for item in AGENT_TEMPLATES if item["slug"] == slug), None)
        if not template:
            raise ValidationError({"template": "Шаблон не найден"})
        agent = Agent.objects.create(
            owner=request.user,
            name=str(request.data.get("name") or template["name"]).strip()[:160],
            role=template["role"],
            objective=str(request.data.get("objective") or template["objective"]).strip(),
            autonomy=template["autonomy"],
            system_level=template["system_level"],
            tool_policy=template["tool_policy"],
            status=Agent.Status.DRAFT,
        )
        return Response(AgentSerializer(agent, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        agent = self.get_object()
        if not agent.objective.strip():
            raise ValidationError({"objective": "Сначала задайте цель агента"})
        agent.status = Agent.Status.ACTIVE
        agent.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(agent).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def run(self, request, pk=None):
        agent = self.get_object()
        if agent.status != Agent.Status.ACTIVE:
            raise ValidationError({"detail": "Сначала активируйте агента"})
        objective = str(request.data.get("objective") or agent.objective).strip()
        if not objective:
            raise ValidationError({"objective": "Укажите задачу запуска"})
        run = AgentRun.objects.create(
            owner=request.user,
            agent=agent,
            project=agent.project,
            objective=objective,
            input_payload=request.data.get("input") or {},
            state=AgentRun.State.QUEUED,
            cost_reserved_rub=Decimal("0"),
        )
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)


class AgentTeamViewSet(viewsets.ModelViewSet):
    serializer_class = AgentTeamSerializer

    def get_queryset(self):
        return AgentTeam.objects.filter(owner=self.request.user).select_related("director", "project").prefetch_related("members__agent")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["post"], url_path="members")
    @transaction.atomic
    def add_member(self, request, pk=None):
        team = self.get_object()
        try:
            agent = Agent.objects.get(id=request.data.get("agent"), owner=request.user)
        except (Agent.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"agent": "Агент не найден"})
        member, _ = AgentTeamMember.objects.update_or_create(
            team=team,
            agent=agent,
            defaults={
                "role": str(request.data.get("role") or agent.role or agent.name).strip()[:160],
                "priority": int(request.data.get("priority") or 100),
                "can_delegate": bool(request.data.get("can_delegate", False)),
                "enabled": True,
            },
        )
        return Response(AgentTeamSerializer(team, context={"request": request}).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def run(self, request, pk=None):
        team = self.get_object()
        if not team.active:
            raise ValidationError({"detail": "Команда приостановлена"})
        objective = str(request.data.get("objective") or team.objective).strip()
        if not objective:
            raise ValidationError({"objective": "Укажите задачу команды"})
        run = AgentRun.objects.create(
            owner=request.user,
            team=team,
            project=team.project,
            objective=objective,
            input_payload=request.data.get("input") or {},
            state=AgentRun.State.QUEUED,
        )
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)


class AgentRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AgentRunSerializer

    def get_queryset(self):
        return AgentRun.objects.filter(owner=self.request.user).select_related("agent", "team", "project").prefetch_related("steps__agent", "approvals")

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        run = self.get_object()
        if run.state in {AgentRun.State.COMPLETED, AgentRun.State.FAILED, AgentRun.State.CANCELED}:
            return Response(self.get_serializer(run).data)
        run.state = AgentRun.State.CANCELED
        run.finished_at = timezone.now()
        run.save(update_fields=["state", "finished_at", "updated_at"])
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["post"], url_path=r"approvals/(?P<approval_id>[^/.]+)/decision")
    @transaction.atomic
    def approval_decision(self, request, pk=None, approval_id=None):
        run = self.get_object()
        approval = AgentApproval.objects.select_for_update().filter(id=approval_id, run=run).first()
        if not approval:
            raise ValidationError({"approval": "Запрос подтверждения не найден"})
        if approval.status != AgentApproval.Status.PENDING:
            return Response(self.get_serializer(run).data)
        decision = str(request.data.get("decision") or "").strip()
        if decision not in {"approved", "rejected"}:
            raise ValidationError({"decision": "Используйте approved или rejected"})
        approval.status = decision
        approval.decided_by = request.user
        approval.decided_at = timezone.now()
        approval.save(update_fields=["status", "decided_by", "decided_at"])
        if decision == "rejected":
            run.state = AgentRun.State.CANCELED
            run.finished_at = timezone.now()
        elif run.state == AgentRun.State.WAITING_APPROVAL:
            run.state = AgentRun.State.RUNNING
        run.save(update_fields=["state", "finished_at", "updated_at"])
        return Response(self.get_serializer(run).data)
