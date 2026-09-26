from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.projects.models import Project

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember
from .planner import draft_from_description, graph_for_kind
from .serializers import AgentRunSerializer, AgentSerializer, AgentTeamSerializer, agent_has_active_run
from .team_builder import team_draft


ACTIVE_RUN_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}

AGENT_TEMPLATES = [
    {
        "slug": "smm-specialist",
        "kind": "smm",
        "name": "SMM-специалист",
        "role": "SMM specialist",
        "objective": "Планировать и создавать контент, проверять факты, готовить материалы к публикации и анализировать результат.",
        "autonomy": Agent.Autonomy.SEMI_AUTONOMOUS,
        "system_level": "balanced",
        "tool_policy": {"web": True, "files": True, "images": True, "publish": "approval"},
    },
    {
        "slug": "seo-copywriter",
        "kind": "seo",
        "name": "SEO-копирайтер",
        "role": "SEO copywriter",
        "objective": "Исследовать тему, собирать факты, писать SEO-материалы, проверять дубли и готовить публикацию.",
        "autonomy": Agent.Autonomy.SEMI_AUTONOMOUS,
        "system_level": "balanced",
        "tool_policy": {"web": True, "files": True, "publish": "approval"},
    },
    {
        "slug": "software-engineer",
        "kind": "development",
        "name": "Разработчик",
        "role": "Software Engineer",
        "objective": "Анализировать код, вносить изменения, запускать проверки и подготавливать безопасные изменения проекта.",
        "autonomy": Agent.Autonomy.CONTROLLED,
        "system_level": "balanced",
        "tool_policy": {"github": True, "files": True, "shell": "sandbox", "write_code": True, "merge": "approval"},
    },
    {
        "slug": "qa-engineer",
        "kind": "qa",
        "name": "QA-инженер",
        "role": "QA Engineer",
        "objective": "Проверять требования, запускать тесты, искать регрессии и оформлять воспроизводимые дефекты.",
        "autonomy": Agent.Autonomy.CONTROLLED,
        "system_level": "balanced",
        "tool_policy": {"github": True, "files": True, "shell": "sandbox", "write_code": False},
    },
]


def _enqueue_run(run):
    from .tasks import execute_agent_run_task

    def enqueue():
        try:
            execute_agent_run_task.delay(str(run.id))
        except Exception as exc:
            now = timezone.now()
            AgentRun.objects.filter(pk=run.id, state=AgentRun.State.QUEUED).update(
                state=AgentRun.State.FAILED,
                error_code="queue_unavailable",
                error_message=str(exc)[:4000],
                finished_at=now,
                updated_at=now,
            )

    transaction.on_commit(enqueue)


def _owned_project(user, raw_id):
    if raw_id in {None, ""}:
        return None
    project = Project.objects.filter(pk=raw_id, owner=user, archived_at__isnull=True).first()
    if project is None:
        raise ValidationError({"project": "Проект недоступен"})
    return project


def _active_run_for_agent(agent):
    return (
        AgentRun.objects.filter(
            Q(agent_id=agent.id) | Q(team__members__agent_id=agent.id, team__members__enabled=True),
            state__in=ACTIVE_RUN_STATES,
        )
        .distinct()
        .order_by("-created_at")
        .first()
    )


def _team_has_active_run(team):
    return AgentRun.objects.filter(team=team, state__in=ACTIVE_RUN_STATES).exists()


def _busy_members_for_team(team):
    members = list(team.members.filter(enabled=True).select_related("agent"))
    conflicts = []
    for membership in members:
        active = _active_run_for_agent(membership.agent)
        if active is not None and active.team_id != team.id:
            conflicts.append((membership.agent, active))
    return conflicts


def _create_dev_agent(user, *, project, name, role, objective, level="balanced", tools=None):
    return Agent.objects.create(
        owner=user,
        project=project,
        name=name,
        role=role,
        objective=objective,
        autonomy=Agent.Autonomy.CONTROLLED,
        status=Agent.Status.ACTIVE,
        system_level=level,
        tool_policy=tools or {},
        graph=graph_for_kind("development" if role != "QA and Security Reviewer" else "qa"),
        max_cost_rub_per_run=Decimal("25"),
        max_steps=80,
        max_tool_calls=120,
        max_handoffs=40,
        max_runtime_seconds=7200,
    )


class AgentViewSet(viewsets.ModelViewSet):
    serializer_class = AgentSerializer

    def get_queryset(self):
        return Agent.objects.filter(owner=self.request.user).select_related("project")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_destroy(self, instance):
        if agent_has_active_run(instance):
            raise ValidationError({"detail": "Нельзя удалить AI-сотрудника во время активного запуска"})
        instance.delete()

    @action(detail=False, methods=["get"], url_path="templates")
    def templates(self, request):
        return Response(AGENT_TEMPLATES)

    @action(detail=False, methods=["post"], url_path="from-description")
    @transaction.atomic
    def from_description(self, request):
        description = str(request.data.get("description") or "").strip()
        if len(description) < 12:
            raise ValidationError({"description": "Опишите, что должен делать сотрудник, чуть подробнее"})
        project = _owned_project(request.user, request.data.get("project"))
        draft = draft_from_description(description)
        agent = Agent.objects.create(
            owner=request.user,
            project=project,
            name=str(request.data.get("name") or draft["name"]).strip()[:160],
            role=draft["role"],
            objective=draft["objective"],
            autonomy=draft["autonomy"],
            system_level=draft["system_level"],
            tool_policy=draft["tool_policy"],
            graph=draft["graph"],
            status=Agent.Status.DRAFT,
        )
        return Response(self.get_serializer(agent).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="from-template")
    @transaction.atomic
    def from_template(self, request):
        slug = str(request.data.get("template") or "").strip()
        template = next((item for item in AGENT_TEMPLATES if item["slug"] == slug), None)
        if not template:
            raise ValidationError({"template": "Шаблон не найден"})
        project = _owned_project(request.user, request.data.get("project"))
        agent = Agent.objects.create(
            owner=request.user,
            project=project,
            name=str(request.data.get("name") or template["name"]).strip()[:160],
            role=template["role"],
            objective=str(request.data.get("objective") or template["objective"]).strip(),
            autonomy=template["autonomy"],
            system_level=template["system_level"],
            tool_policy=template["tool_policy"],
            graph=graph_for_kind(template["kind"]),
            status=Agent.Status.DRAFT,
        )
        return Response(AgentSerializer(agent, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        agent = self.get_object()
        if agent_has_active_run(agent):
            raise ValidationError({"detail": "Нельзя менять состояние AI-сотрудника во время активного запуска"})
        if not agent.objective.strip():
            raise ValidationError({"objective": "Сначала задайте цель агента"})
        agent.status = Agent.Status.ACTIVE
        agent.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(agent).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def run(self, request, pk=None):
        scoped = self.get_object()
        agent = Agent.objects.select_for_update().get(pk=scoped.pk)
        if agent.status != Agent.Status.ACTIVE:
            raise ValidationError({"detail": "Сначала активируйте агента"})
        existing = _active_run_for_agent(agent)
        if existing is not None:
            if existing.agent_id == agent.id:
                return Response(AgentRunSerializer(existing).data, status=status.HTTP_200_OK)
            raise ValidationError({"detail": "AI-сотрудник уже занят активной задачей команды"})
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
        _enqueue_run(run)
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)


class AgentTeamViewSet(viewsets.ModelViewSet):
    serializer_class = AgentTeamSerializer

    def get_queryset(self):
        return AgentTeam.objects.filter(owner=self.request.user).select_related("director", "project").prefetch_related("members__agent")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_destroy(self, instance):
        if _team_has_active_run(instance):
            raise ValidationError({"detail": "Нельзя удалить команду во время активного запуска"})
        instance.delete()

    @action(detail=False, methods=["post"], url_path="from-description")
    @transaction.atomic
    def from_description(self, request):
        description = str(request.data.get("description") or "").strip()
        if len(description) < 12:
            raise ValidationError({"description": "Опишите задачу команды чуть подробнее"})
        project = _owned_project(request.user, request.data.get("project"))
        draft = team_draft(description)
        if draft["kind"] == AgentTeam.Kind.DEVELOPMENT:
            raise ValidationError({"description": "Для разработки используйте Dev Studio и подключённый GitHub-проект"})
        created = []
        for index, member in enumerate(draft["members"], start=1):
            agent = Agent.objects.create(
                owner=request.user,
                project=project,
                name=member["name"],
                role=member["role"],
                objective=f"Работать в роли {member['role']} для задач команды. Общую цель определяет запуск команды.",
                autonomy=Agent.Autonomy.SEMI_AUTONOMOUS,
                status=Agent.Status.ACTIVE,
                system_level=member["level"],
                tool_policy=member["tools"],
                graph=member["graph"],
                max_cost_rub_per_run=Decimal("20"),
                max_steps=50,
                max_tool_calls=60,
                max_handoffs=20,
                max_runtime_seconds=3600,
            )
            created.append((index, agent, member["role"]))
        director = created[0][1]
        team = AgentTeam.objects.create(
            owner=request.user,
            project=project,
            name=str(request.data.get("name") or draft["name"]).strip()[:160],
            objective=description,
            kind=draft["kind"],
            director=director,
            max_cost_rub_per_run=Decimal("75"),
            max_handoffs=30,
        )
        for index, agent, role in created:
            AgentTeamMember.objects.create(
                team=team,
                agent=agent,
                role=role,
                priority=index * 10,
                can_delegate=index == 1,
                enabled=True,
            )
        return Response(AgentTeamSerializer(team, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="bootstrap-dev")
    @transaction.atomic
    def bootstrap_dev(self, request):
        objective = str(request.data.get("objective") or "").strip()
        if not objective:
            raise ValidationError({"objective": "Опишите задачу разработки"})
        project = _owned_project(request.user, request.data.get("project"))
        if project is None:
            raise ValidationError({"project": "Для Dev Studio выберите проект"})
        if not hasattr(project, "github_repository"):
            raise ValidationError({"project": "Сначала подключите GitHub repository к проекту"})
        director = _create_dev_agent(request.user, project=project, name="Engineering Director", role="Engineering Director", objective="Руководить разработкой: анализировать задачу, строить план, делегировать, принимать результаты и контролировать качество.", level="maximum", tools={"github": True, "files": True, "delegate": True, "approve": True})
        architect = _create_dev_agent(request.user, project=project, name="Software Architect", role="Software Architect", objective="Изучать архитектуру, зависимости и риски до изменения кода. Формировать технический план.", level="maximum", tools={"github": True, "files": True, "write_code": False})
        engineer = _create_dev_agent(request.user, project=project, name="Software Engineer", role="Software Engineer", objective="Вносить минимальные безопасные изменения в код по утверждённому плану и запускать проверки.", tools={"github": True, "files": True, "shell": "sandbox", "write_code": True, "merge": "approval"})
        qa = _create_dev_agent(request.user, project=project, name="QA & Security", role="QA and Security Reviewer", objective="Проверять тесты, регрессии, безопасность и готовность изменений к публикации.", tools={"github": True, "files": True, "shell": "sandbox", "write_code": False})
        team = AgentTeam.objects.create(
            owner=request.user,
            project=project,
            name=str(request.data.get("name") or f"Dev Team · {project.name}").strip()[:160],
            objective=objective,
            kind=AgentTeam.Kind.DEVELOPMENT,
            director=director,
            max_cost_rub_per_run=Decimal("100"),
            max_handoffs=60,
        )
        for priority, (agent, role, can_delegate) in enumerate([(director, "Engineering Director", True), (architect, "Architecture", False), (engineer, "Development", False), (qa, "QA & Security", False)], start=1):
            AgentTeamMember.objects.create(team=team, agent=agent, role=role, priority=priority * 10, can_delegate=can_delegate)
        return Response(AgentTeamSerializer(team, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="members")
    @transaction.atomic
    def add_member(self, request, pk=None):
        scoped = self.get_object()
        team = AgentTeam.objects.select_for_update().get(pk=scoped.pk, owner=request.user)
        if _team_has_active_run(team):
            raise ValidationError({"detail": "Нельзя менять состав команды во время активного запуска"})
        try:
            agent = Agent.objects.get(id=request.data.get("agent"), owner=request.user)
        except (Agent.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"agent": "Агент не найден"})
        if agent_has_active_run(agent):
            raise ValidationError({"agent": "AI-сотрудник сейчас занят другим активным запуском"})
        if team.project_id and agent.project_id not in {None, team.project_id}:
            raise ValidationError({"agent": "Агент привязан к другому проекту"})
        AgentTeamMember.objects.update_or_create(
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
        scoped = self.get_object()
        team = AgentTeam.objects.select_for_update().get(pk=scoped.pk)
        if not team.active:
            raise ValidationError({"detail": "Команда приостановлена"})
        existing = (
            AgentRun.objects.filter(owner=request.user, team=team, state__in=ACTIVE_RUN_STATES)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            return Response(AgentRunSerializer(existing).data, status=status.HTTP_200_OK)
        conflicts = _busy_members_for_team(team)
        if conflicts:
            names = ", ".join(agent.name for agent, _run in conflicts[:5])
            raise ValidationError({"detail": f"Нельзя запустить команду: уже заняты участники — {names}"})
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
        _enqueue_run(run)
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)


class AgentRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AgentRunSerializer

    def get_queryset(self):
        queryset = (
            AgentRun.objects.filter(owner=self.request.user)
            .select_related("agent", "team", "project")
            .prefetch_related(
                "steps__agent",
                "approvals",
                "handoffs__from_agent",
                "handoffs__to_agent",
            )
        )
        agent_id = str(self.request.query_params.get("agent") or "").strip()
        team_id = str(self.request.query_params.get("team") or "").strip()
        if agent_id:
            queryset = queryset.filter(agent_id=agent_id)
        if team_id:
            queryset = queryset.filter(team_id=team_id)
        return queryset

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        run = self.get_object()
        if run.state in {AgentRun.State.COMPLETED, AgentRun.State.FAILED, AgentRun.State.CANCELED}:
            return Response(self.get_serializer(run).data)
        run.state = AgentRun.State.CANCELED
        run.finished_at = timezone.now()
        run.save(update_fields=["state", "finished_at", "updated_at"])
        return Response(self.get_serializer(run).data)
