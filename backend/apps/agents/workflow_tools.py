import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.web_tools import WebToolError, search_context
from apps.connections.http_service import connection_request
from apps.connections.models import AgentConnectionBinding, ExternalConnection

from .browser_tool import read_browser_page
from .graph_runtime import _fail, _previous_text
from .models import AgentApproval, AgentRun, AgentStepRun


def render_body(value, *, objective, previous_text):
    if isinstance(value, str):
        return value.replace("{{objective}}", objective).replace("{{previous_text}}", previous_text)
    if isinstance(value, dict):
        return {key: render_body(item, objective=objective, previous_text=previous_text) for key, item in value.items()}
    if isinstance(value, list):
        return [render_body(item, objective=objective, previous_text=previous_text) for item in value]
    return value


def workflow_connection(agent, node):
    binding = AgentConnectionBinding.objects.filter(agent=agent, connection_id=node.get("connection_id"), enabled=True, purpose="http", connection__owner_id=agent.owner_id, connection__kind=ExternalConnection.Kind.HTTP, connection__enabled=True, connection__health_state=ExternalConnection.Health.HEALTHY).select_related("connection").first()
    if binding is None:
        raise ValidationError("Разрешите этому агенту использовать рабочее HTTP-подключение")
    return binding.connection


def run_workflow_tool(run, agent, node, sequence):
    kind = node["type"]
    node_id = node["id"]
    policy_key = "web" if kind == "search" else kind
    if not (agent.tool_policy or {}).get(policy_key):
        return _fail(run, None, "workflow_tool_disabled", f"Инструмент {kind} не разрешён агенту")
    try:
        connection = workflow_connection(agent, node) if kind == "http" else None
        if kind == "http" and node.get("method", "GET") == "POST":
            body = render_body(node.get("body", {}), objective=run.objective, previous_text=_previous_text(run))
            body_json = json.dumps(body, ensure_ascii=False, sort_keys=True)
            fingerprint = hashlib.sha256(body_json.encode()).hexdigest()
            with transaction.atomic():
                locked = AgentRun.objects.select_for_update().get(pk=run.pk)
                if locked.state == AgentRun.State.CANCELED:
                    run.refresh_from_db()
                    return run
                approval = run.approvals.filter(action_payload__kind="http_request", action_payload__node_id=node_id, action_payload__body_digest=fingerprint).order_by("-created_at").first()
                if approval and approval.status in {AgentApproval.Status.REJECTED, AgentApproval.Status.EXPIRED}:
                    return _fail(run, None, "http_approval_rejected", "HTTP-запрос не подтверждён")
                if not approval or approval.status != AgentApproval.Status.APPROVED:
                    if approval is None:
                        AgentApproval.objects.create(run=run, requested_by_agent=agent, title=f"POST в {connection.name}: {node.get('path') or '/'}"[:240], description="Отправить JSON в подключённый сервис:\n" + body_json[:4000], action_payload={"kind": "http_request", "node_id": node_id, "connection_id": str(connection.id), "body_digest": fingerprint})
                    run.state = AgentRun.State.WAITING_APPROVAL
                    run.save(update_fields=["state", "updated_at"])
                    return run
        with transaction.atomic():
            locked = AgentRun.objects.select_for_update().get(pk=run.pk)
            if locked.state == AgentRun.State.CANCELED:
                run.refresh_from_db()
                return run
            step, created = AgentStepRun.objects.get_or_create(run=run, node_id=node_id, attempt=1, defaults={"agent": agent, "sequence": sequence, "title": node["title"], "action_type": kind, "state": AgentStepRun.State.RUNNING, "started_at": timezone.now()})
            if not created:
                if step.state == AgentStepRun.State.COMPLETED:
                    return None
                return _fail(run, step, "workflow_action_outcome_unknown", "Предыдущая попытка этого шага не завершена. Проверьте внешний сервис перед новым запуском")
            locked.tool_call_count += 1
            locked.save(update_fields=["tool_call_count", "updated_at"])
            run.tool_call_count = locked.tool_call_count
        if kind == "http":
            body = render_body(node.get("body", {}), objective=run.objective, previous_text=_previous_text(run))
            text = connection_request(connection, path=node.get("path", ""), method=node.get("method", "GET"), payload=body if node.get("method") == "POST" else None, operation_key=f"agent:{run.id}:{node_id}")
            output = {"text": text[:18000], "connection_id": str(connection.id), "method": node.get("method", "GET")}
        elif kind == "browser":
            output = read_browser_page(node["url"], node.get("prompt", ""))
            if node.get("prompt"):
                output["text"] = output["matches"] or "Указанный текст на странице не найден."
        else:
            text, sources = search_context(node.get("prompt") or run.objective, limit=5)
            if not text.strip():
                raise WebToolError("Поиск не вернул результаты")
            output = {"text": text[:18000], "sources": sources}
        run.refresh_from_db(fields=["state"])
        step.state = AgentStepRun.State.COMPLETED
        step.output_payload = output
        step.public_log = f"{node['title']}: инструмент выполнен."
        step.finished_at = timezone.now()
        step.save(update_fields=["state", "output_payload", "public_log", "finished_at"])
        if run.state == AgentRun.State.CANCELED:
            return run
        return None
    except Exception as exc:
        step = locals().get("step")
        uncertain = kind == "http" and node.get("method") == "POST" and step is not None
        message = "Результат POST не подтверждён. Проверьте внешний сервис перед повторным запуском. " + str(exc) if uncertain else str(exc)
        return _fail(run, step, "workflow_action_outcome_unknown" if uncertain else "workflow_tool_failed", message)
