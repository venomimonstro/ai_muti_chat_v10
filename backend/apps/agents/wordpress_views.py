import re

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.connections.models import AgentConnectionBinding, ExternalConnection
from apps.connections.wordpress import create_wordpress_post, update_wordpress_post

from .models import AgentRun, AgentStepRun


def _default_title(run, text):
    for line in str(text or "").splitlines():
        candidate = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if 4 <= len(candidate) <= 180:
            return candidate
    return str(run.objective or "Материал AI Workspace").strip()[:180]


def _run_text(run):
    text = str((run.output_payload or {}).get("text") or "").strip()
    if text:
        return text
    step = (
        run.steps.filter(state=AgentStepRun.State.COMPLETED)
        .order_by("-sequence", "-created_at")
        .first()
    )
    if not step:
        return ""
    return str((step.output_payload or {}).get("text") or step.public_log or "").strip()


def _connection_for(run, requested_id):
    bindings = (
        AgentConnectionBinding.objects.filter(
            agent=run.agent,
            purpose="publish",
            enabled=True,
            connection__enabled=True,
            connection__kind=ExternalConnection.Kind.WORDPRESS,
        )
        .select_related("connection")
        .order_by("created_at")
    )
    if requested_id:
        binding = bindings.filter(connection_id=requested_id).first()
        if binding is None:
            raise ValidationError({"connection": "WordPress не подключён к этому агенту"})
        return binding.connection
    rows = list(bindings[:2])
    if not rows:
        raise ValidationError({"connection": "Сначала подключите WordPress к агенту"})
    if len(rows) > 1:
        raise ValidationError({"connection": "Выберите WordPress для публикации"})
    return rows[0].connection


class AgentRunWordPressView(APIView):
    @transaction.atomic
    def post(self, request, run_id):
        run = get_object_or_404(
            AgentRun.objects.select_for_update().select_related("agent"),
            id=run_id,
            owner=request.user,
        )
        if not run.agent_id:
            raise ValidationError({"detail": "Публикация WordPress пока доступна для одиночного агента"})
        if run.state != AgentRun.State.COMPLETED:
            raise ValidationError({"detail": "Публиковать можно только завершённый результат"})
        if (run.agent.tool_policy or {}).get("publish") != "approval":
            raise ValidationError({"detail": "Сначала разрешите публикацию в настройках агента"})
        target_status = str(request.data.get("status") or "draft").strip().lower()
        if target_status not in {"draft", "publish"}:
            raise ValidationError({"status": "Используйте draft или publish"})
        connection = _connection_for(run, str(request.data.get("connection") or "").strip())
        if connection.health_state != ExternalConnection.Health.HEALTHY:
            raise ValidationError({"connection": "Сначала проверьте WordPress в разделе «Подключения»"})

        text = _run_text(run)
        if not text:
            raise ValidationError({"detail": "В запуске нет готового текста для WordPress"})
        if len(text) > 500000:
            raise ValidationError({"detail": "Материал слишком большой для автоматической публикации"})
        title = str(request.data.get("title") or "").strip() or _default_title(run, text)
        output = dict(run.output_payload or {})
        publication = output.get("wordpress_publication")
        if isinstance(publication, dict) and publication.get("post_id"):
            if str(publication.get("connection_id")) != str(connection.id):
                raise ValidationError({"connection": "Этот run уже связан с другим WordPress постом"})
            if publication.get("status") == "publish" or target_status == publication.get("status"):
                return Response({"publication": publication, "created": False})
            result = update_wordpress_post(
                connection,
                post_id=publication["post_id"],
                status=target_status,
                title=title,
                content=text,
            )
            created = False
        else:
            result = create_wordpress_post(
                connection,
                title=title,
                content=text,
                status=target_status,
            )
            created = True
        publication = {
            **result,
            "connection_id": str(connection.id),
            "connection_name": connection.name,
            "title": title,
        }
        output["wordpress_publication"] = publication
        run.output_payload = output
        run.save(update_fields=["output_payload", "updated_at"])
        return Response({"publication": publication, "created": created})
