from django.core.exceptions import ValidationError

from apps.connections.models import AgentConnectionBinding, ExternalConnection

from .models import Agent
from .runtime import _model_for
from .wait_runtime import MAX_WAIT_MINUTES, MIN_WAIT_MINUTES


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
    "condition",
    "notify",
    "wait",
    "finish",
}

CONDITION_OPERATORS = {"contains", "not_contains", "is_empty", "not_empty"}
PUBLIC_LEVELS = {
    "economy": "System Lite",
    "balanced": "System Pro",
    "maximum": "System Max",
}


def agent_readiness(agent: Agent):
    blockers = []
    warnings = []
    checks = {}
    actions = []
    action_codes = set()

    def add_action(code, label, href=""):
        if code in action_codes:
            return
        action_codes.add(code)
        actions.append({"code": code, "label": label, "href": href})

    objective_ok = bool(str(agent.objective or "").strip())
    checks["objective"] = objective_ok
    if not objective_ok:
        blockers.append("Задайте главную цель сотрудника")
        add_action("set_objective", "Заполните главную цель в блоке «Роль и задача»")

    graph = agent.graph if isinstance(agent.graph, dict) else {}
    nodes = [node for node in (graph.get("nodes") or []) if isinstance(node, dict)]
    node_types = [str(node.get("type") or "llm").strip().lower() for node in nodes]
    visual_workflow = bool(nodes)
    checks["graph"] = visual_workflow

    node_ids = [str(node.get("id") or "").strip() for node in nodes]
    known_ids = {node_id for node_id in node_ids if node_id}
    if len(known_ids) != len(node_ids):
        blockers.append("У каждого шага карты должен быть уникальный ID")
        add_action("fix_graph_ids", "Пересохраните карту действий")

    for node in nodes:
        node_type = str(node.get("type") or "").strip().lower()
        node_title = str(node.get("title") or "Шаг")
        if node_type == "condition":
            operator = str(node.get("operator") or "contains").strip().lower()
            if operator not in CONDITION_OPERATORS:
                blockers.append(f"«{node_title}»: выбрано неподдерживаемое условие")
            if operator in {"contains", "not_contains"} and not str(node.get("value") or "").strip():
                blockers.append(f"«{node_title}»: укажите текст для проверки")
            for field, label in (("on_true", "Да"), ("on_false", "Нет")):
                target = str(node.get(field) or "").strip()
                if target and target not in known_ids:
                    blockers.append(f"«{node_title}»: ветка «{label}» ведёт к отсутствующему шагу")
            if str(node.get("on_true") or "").strip() == str(node.get("id") or "").strip() or str(node.get("on_false") or "").strip() == str(node.get("id") or "").strip():
                blockers.append(f"«{node_title}»: условие не может вести само в себя")
        elif node_type == "wait":
            try:
                minutes = int(node.get("wait_minutes") or 60)
            except (TypeError, ValueError):
                minutes = 0
            if not MIN_WAIT_MINUTES <= minutes <= MAX_WAIT_MINUTES:
                blockers.append(f"«{node_title}»: ожидание должно быть от 1 минуты до 7 дней")

    public_model = PUBLIC_LEVELS.get(agent.system_level, "System Pro")
    if not visual_workflow:
        checks["runtime_nodes"] = True
        checks["model"] = True
        warnings.append(
            "Используется простой режим без визуальной карты. Модель будет проверена непосредственно перед выполнением без списания средств."
        )
    else:
        unsupported = sorted({node_type for node_type in node_types if node_type not in RUNTIME_NODE_TYPES})
        checks["runtime_nodes"] = not unsupported
        if unsupported:
            blockers.append("Карта содержит шаги Dev Studio или неподдерживаемые действия: " + ", ".join(unsupported))
            add_action("fix_graph", "Удалите неподдерживаемые шаги из карты действий")

        needs_model = any(node_type in {"llm", "review", "analytics", "research", "web", "files"} for node_type in node_types)
        if needs_model:
            try:
                _model_for(agent)
                checks["model"] = True
            except ValidationError as exc:
                checks["model"] = False
                blockers.append(str(exc))
                add_action("model_unavailable", f"Уровень {public_model} сейчас недоступен. Проверьте маршрутизацию у администратора")
        else:
            checks["model"] = True

    policy = agent.tool_policy or {}
    if any(node_type in {"web", "research"} for node_type in node_types):
        web_ok = bool(policy.get("web"))
        checks["web"] = web_ok
        if not web_ok:
            blockers.append("Карта использует интернет, но доступ к web-поиску выключен")
            add_action("enable_web", "Включите «Интернет и поиск» в разрешениях сотрудника")

    if "files" in node_types:
        files_ok = bool(policy.get("files")) and bool(agent.project_id)
        checks["files"] = files_ok
        if not bool(policy.get("files")):
            blockers.append("Карта использует файлы, но доступ к файлам выключен")
            add_action("enable_files", "Включите доступ к файлам в разрешениях сотрудника")
        elif not agent.project_id:
            blockers.append("Для шага «Файлы проекта» привяжите сотрудника к проекту")
            add_action("attach_project", "Выберите или создайте проект", "/app/projects")

    if "image" in node_types:
        images_ok = bool(policy.get("images"))
        checks["images"] = images_ok
        if not images_ok:
            blockers.append("Карта создаёт изображения, но Image Studio выключена для сотрудника")
            add_action("enable_images", "Включите создание изображений в разрешениях сотрудника")

    if "publish" in node_types:
        publish_policy = str(policy.get("publish") or "disabled").strip().lower()
        policy_ok = publish_policy in {"approval", "auto", "autonomous", "true"}
        checks["publish_policy"] = policy_ok
        if not policy_ok:
            blockers.append("Карта содержит публикацию, но публикация не разрешена")
            add_action("enable_publish", "Разрешите публикацию с подтверждением в настройках сотрудника")

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
            add_action("connect_wordpress", "Подключите сайт WordPress", "/app/connections")
        elif len(bindings) > 1:
            blockers.append("Для публикации оставьте одно активное подключение WordPress")
            add_action("choose_wordpress", "Оставьте одно подключение WordPress для публикации", "/app/connections")
        elif bindings[0].connection.health_state != ExternalConnection.Health.HEALTHY:
            blockers.append("WordPress не прошёл проверку подключения")
            add_action("repair_wordpress", "Проверьте подключение WordPress", "/app/connections")

    if agent.status != Agent.Status.ACTIVE:
        warnings.append("Сотрудник ещё не активирован")
        add_action("activate", "Активируйте сотрудника после завершения настройки")

    return {
        "ready": not blockers,
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "actions": actions,
        "model": public_model,
    }


def require_agent_ready(agent: Agent):
    result = agent_readiness(agent)
    if not result["ready"]:
        raise ValidationError("; ".join(result["blockers"]))
    return result
