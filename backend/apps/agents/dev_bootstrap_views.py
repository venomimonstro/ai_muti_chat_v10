from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AgentTeam, AgentTeamMember
from .serializers import AgentTeamSerializer
from .views import _create_dev_agent, _owned_project


class DevTeamBootstrapView(APIView):
    """Create one Dev Team safely; retrying the same bootstrap reuses a fresh duplicate."""

    @transaction.atomic
    def post(self, request):
        objective = str(request.data.get("objective") or "").strip()
        if not objective:
            raise ValidationError({"objective": "Опишите задачу разработки"})
        project = _owned_project(request.user, request.data.get("project"))
        if project is None:
            raise ValidationError({"project": "Для Dev Studio выберите проект"})
        try:
            binding = project.github_repository
        except Exception:
            binding = None
        if binding is None:
            raise ValidationError({"project": "Сначала подключите GitHub repository к проекту"})
        if not binding.installation.active:
            raise ValidationError({"project": "GitHub App installation отключена. Переподключите GitHub"})
        contents_permission = str((binding.installation.permissions or {}).get("contents") or "").strip().lower()
        if contents_permission not in {"write", "admin"}:
            raise ValidationError({"project": "Dev Studio требует GitHub permission contents:write"})
        if not binding.write_enabled:
            raise ValidationError({"project": "Разрешите Dev Studio запись в рабочую ветку GitHub"})

        # Serialize bootstrap attempts for one project. Network retries/double clicks
        # within a short window must not create another 4 service agents and team.
        project.__class__.objects.select_for_update().get(pk=project.pk, owner=request.user)
        cutoff = timezone.now() - timedelta(seconds=90)
        duplicate = (
            AgentTeam.objects.filter(
                owner=request.user,
                project=project,
                kind=AgentTeam.Kind.DEVELOPMENT,
                objective=objective,
                active=True,
                created_at__gte=cutoff,
            )
            .prefetch_related("members__agent")
            .select_related("director", "project")
            .order_by("-created_at")
            .first()
        )
        if duplicate is not None:
            payload = AgentTeamSerializer(duplicate, context={"request": request}).data
            payload["reused"] = True
            return Response(payload, status=status.HTTP_200_OK)

        director = _create_dev_agent(
            request.user,
            project=project,
            name="Engineering Director",
            role="Engineering Director",
            objective="Руководить разработкой: анализировать задачу, строить план, делегировать, принимать результаты и контролировать качество.",
            level="maximum",
            tools={"github": True, "files": True, "delegate": True, "approve": True},
        )
        architect = _create_dev_agent(
            request.user,
            project=project,
            name="Software Architect",
            role="Software Architect",
            objective="Изучать архитектуру, зависимости и риски до изменения кода. Формировать технический план.",
            level="maximum",
            tools={"github": True, "files": True, "write_code": False},
        )
        engineer = _create_dev_agent(
            request.user,
            project=project,
            name="Software Engineer",
            role="Software Engineer",
            objective="Вносить минимальные безопасные изменения в код по утверждённому плану и запускать проверки.",
            tools={"github": True, "files": True, "shell": "sandbox", "write_code": True, "merge": "approval"},
        )
        qa = _create_dev_agent(
            request.user,
            project=project,
            name="QA & Security",
            role="QA and Security Reviewer",
            objective="Проверять тесты, регрессии, безопасность и готовность изменений к публикации.",
            tools={"github": True, "files": True, "shell": "sandbox", "write_code": False},
        )
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
        for priority, (agent, role, can_delegate) in enumerate(
            [
                (director, "Engineering Director", True),
                (architect, "Architecture", False),
                (engineer, "Development", False),
                (qa, "QA & Security", False),
            ],
            start=1,
        ):
            AgentTeamMember.objects.create(
                team=team,
                agent=agent,
                role=role,
                priority=priority * 10,
                can_delegate=can_delegate,
            )
        payload = AgentTeamSerializer(team, context={"request": request}).data
        payload["reused"] = False
        return Response(payload, status=status.HTTP_201_CREATED)
