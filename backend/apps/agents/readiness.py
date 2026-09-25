from django.core.exceptions import ValidationError

from apps.connections.models import AgentConnectionBinding, ExternalConnection

from .models import Agent
from .runtime import _model_for


RUNTIME_NODE_TYPES = {
    "llm",
    "research",
    "web",
    "files",
    "image",
    "review",
    "approval",
    "publish",
    "analytics",
}


def agent_readiness(agent: Agent):
    blockers = []
    warnings = []
    checks = {}

    objective_ok = bool(str(agent.objective or "").strip())
    checks["objective"] = objective_ok
    if not objective_ok:
        blockers.append("Задайте главную цель сотрудника")

    graph = agent.graph if isinstance(agent.graph, dict) else {}
    nodes = [node for node in (graph.get("nodes") or []) if isinstance(node, dict)]
    node_types = [str(node.get("type") or "llm").strip().lower() for node in nodes]
    checks["graph"] = bool(nodes)
    if not nodes:
        blockers.append("Карта действий пуста")

    unsupported = sorted({node_type for node_type in node_types if node_type not in RUNTIME_NODE_TYPES})
    checks["runtime_nodes"] = not unsupported
    if unsupported:
        blockers.append("Карта содержит шаги Dev Studio или неподдерживаемые действия: " + ", ".join(unsupported))

    try:
        model = _model_for(agent)
        checks["model"] = True
        model_name = model.slug
    except ValidationError as exc:
        checks["model"] = False
        model_name = ""
        blockers.append(str(exc))

    policy = agent.tool_policy or {}
    if any(node_type in {"web", "research"} for node_type in node_types):
        web_ok = bool(policy.get("web"))
        checks["web"] = web_ok
        if not web_ok:
            blockers.append("Карта использует интернет, но доступ к web-поиску выключен")

    if "files" in node_types:
        files_ok = bool(policy.get("files")) and bool(agent.project_id)
        checks["files"] = files_ok
        if not bool(policy.get("files")):
            blockers.append("Карта использует файлы, но доступ к файлам выключен")
        elif not agent.project_id:
            blockers.append("Для шага «Файлы проекта» привяжите сотрудника к проекту")

    if "image" in node_types:
        images_ok = bool(policy.get("images"))
        checks["images"] = images_ok
        if not images_ok:
            blockers.append("Карта создаёт изображения, но Image Studio выключена для сотрудника")

    if "publish" in node_types:
        publish_policy = str(policy.get("publish") or "disabled").strip().lower()
        policy_ok = publish_policy in {"approval", "auto", "autonomous", "true"}
        checks["publish_policy"] = policy_ok
        if not policy_ok:
            blockers.append("Карта содержит публикацию, но публикация не разрешена")

        bindings = list(
            AgentConnectionBinding.objects.filter(
                agent=agent,
                purpose="publish",
                enabled=True,
                connection__enabled=True,
                connection__kind=ExternalConnection.Kind.WORDPRESS,
            )
            .select_related("connection")
            .order_by("created_at")[:2]
        )
        wordpress_ok = len(bindings) == 1 and bindings[0].connection.health_state == ExternalConnection.Health.HEALTHY
        checks["wordpress"] = wordpress_ok
        if not bindings:
            blockers.append("Подключите WordPress для шага публикации")
        elif len(bindings) > 1:
            blockers.append("Для публикации оставьте одно активное подключение WordPress")
        elif bindings[0].connection.health_state != ExternalConnection.Health.HEALTHY:
            blockers.append("WordPress не прошёл проверку подключения")

    if agent.status != Agent.Status.ACTIVE:
        warnings.append("Сотрудник ещё не активирован")

    return {
        "ready": not blockers,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "model": model_name,
    }


def require_agent_ready(agent: Agent):
    result = agent_readiness(agent)
    if not result["ready"]:
        raise ValidationError("; ".join(result["blockers"]))
    return result
