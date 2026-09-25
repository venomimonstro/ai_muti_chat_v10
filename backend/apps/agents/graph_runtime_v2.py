from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Notification

from .graph_runtime import (
    SUPPORTED_LLM_NODES,
    UNSUPPORTED_EXTERNAL_NODES,
    _budget_exceeded,
    _fail,
    _handle_approval,
    _planned_tool_calls,
    _previous_text,
    _run_image_node,
    _run_llm_node,
    _skip_external,
)
from .limits import effective_remaining_budget
from .models import AgentRun, AgentStepRun
from .wait_runtime import handle_wait_node

DETERMINISTIC_NODES = {"condition", "notify", "finish", "wait"}


def _node_id(node, index):
    return str(node.get("id") or f"node-{index + 1}")


def _graph_parts(agent):
    graph = agent.graph if isinstance(agent.graph, dict) else {}
    nodes = [item for item in (graph.get("nodes") or []) if isinstance(item, dict)]
    edges = [item for item in (graph.get("edges") or []) if isinstance(item, dict)]
    node_ids = [_node_id(node, index) for index, node in enumerate(nodes)]
    by_id = {node_id: nodes[index] for index, node_id in enumerate(node_ids)}
    position = {node_id: index for index, node_id in enumerate(node_ids)}
    outgoing = {}
    for edge in edges:
        source = str(edge.get("from") or "").strip()
        target = str(edge.get("to") or "").strip()
        if source in by_id and target in by_id:
            outgoing.setdefault(source, []).append(target)
    return nodes, node_ids, by_id, position, outgoing


def _default_next(node_id, node_ids, position, outgoing):
    explicit = outgoing.get(node_id) or []
    if explicit:
        return explicit[0]
    index = position[node_id]
    return node_ids[index + 1] if index + 1 < len(node_ids) else None


def _condition_text(run, node):
    source = str(node.get("condition_source") or "previous_text").strip().lower()
    if source == "objective":
        return str(run.objective or "")
    return _previous_text(run)


def _condition_result(run, node):
    text = _condition_text(run, node)
    operator = str(node.get("operator") or "contains").strip().lower()
    value = str(node.get("value") or "")
    left = text.casefold()
    right = value.casefold()
    if operator == "contains":
        return right in left
    if operator == "not_contains":
        return right not in left
    if operator == "is_empty":
        return not bool(text.strip())
    if operator == "not_empty":
        return bool(text.strip())
    raise ValueError(f"Неподдерживаемое условие: {operator}")


def _run_condition(run, agent, node, sequence, node_ids, position, outgoing):
    node_id = str(node.get("id") or f"condition-{sequence}")
    existing = run.steps.filter(node_id=node_id, state=AgentStepRun.State.COMPLETED).first()
    if existing:
        payload = existing.output_payload or {}
        return str(payload.get("next_node") or "") or None

    try:
        matched = _condition_result(run, node)
    except ValueError as exc:
        _fail(run, None, "graph_condition_invalid", str(exc))
        return "__terminal__"

    target = str(node.get("on_true" if matched else "on_false") or "").strip()
    if not target:
        target = _default_next(node_id, node_ids, position, outgoing)
    title = str(node.get("title") or "Условие")[:240]
    AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=sequence,
        node_id=node_id,
        title=title,
        action_type="condition",
        state=AgentStepRun.State.COMPLETED,
        input_payload={
            "source": str(node.get("condition_source") or "previous_text"),
            "operator": str(node.get("operator") or "contains"),
            "value": str(node.get("value") or ""),
        },
        output_payload={"matched": matched, "next_node": target or ""},
        public_log=f"Условие {'выполнено' if matched else 'не выполнено'}. Выбран следующий маршрут.",
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )
    run.step_count = max(run.step_count, sequence)
    run.save(update_fields=["step_count", "updated_at"])
    return target


def _run_notify(run, agent, node, sequence):
    node_id = str(node.get("id") or f"notify-{sequence}")
    if run.steps.filter(node_id=node_id, state=AgentStepRun.State.COMPLETED).exists():
        return
    title = str(node.get("notification_title") or node.get("title") or "Сообщение от AI-сотрудника")[:160]
    previous = _previous_text(run)
    body = str(node.get("message") or previous or run.objective or "Задача требует вашего внимания.").strip()[:4000]
    Notification.objects.get_or_create(
        user=run.owner,
        dedupe_key=f"agent-node:{run.id}:{node_id}",
        defaults={
            "title": title,
            "body": body,
            "level": Notification.Level.INFO,
            "action_url": f"/app/runs/{run.id}",
        },
    )
    AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=sequence,
        node_id=node_id,
        title=str(node.get("title") or "Уведомить пользователя")[:240],
        action_type="notify",
        state=AgentStepRun.State.COMPLETED,
        output_payload={"notification": True},
        public_log="Пользователю отправлено уведомление внутри AI Workspace.",
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )
    run.step_count = max(run.step_count, sequence)
    run.save(update_fields=["step_count", "updated_at"])


def _run_finish(run, agent, node, sequence):
    node_id = str(node.get("id") or f"finish-{sequence}")
    AgentStepRun.objects.get_or_create(
        run=run,
        node_id=node_id,
        attempt=1,
        defaults={
            "agent": agent,
            "sequence": sequence,
            "title": str(node.get("title") or "Завершить")[:240],
            "action_type": "finish",
            "state": AgentStepRun.State.COMPLETED,
            "public_log": "Workflow завершён по маршруту карты действий.",
            "started_at": timezone.now(),
            "finished_at": timezone.now(),
        },
    )
    run.step_count = max(run.step_count, sequence)
    run.save(update_fields=["step_count", "updated_at"])


def _finish_run(run, nodes, node_ids, route):
    run.refresh_from_db()
    if run.state == AgentRun.State.CANCELED:
        return run
    completed = list(run.steps.filter(state=AgentStepRun.State.COMPLETED).order_by("sequence", "created_at"))
    final_text = ""
    web_sources = []
    file_sources = []
    image_generations = []
    for step in completed:
        payload = step.output_payload or {}
        text = str(payload.get("text") or "").strip()
        if text:
            final_text = text
        web_sources.extend(payload.get("web_sources") or [])
        file_sources.extend(payload.get("file_sources") or [])
        if payload.get("image_generation"):
            image_generations.append(payload["image_generation"])
    run.state = AgentRun.State.COMPLETED
    run.output_payload = {
        "text": final_text,
        "workflow": "graph_v2",
        "route": route,
        "web_sources": list({item["id"]: item for item in web_sources if item.get("id")}.values()),
        "file_sources": list({item["id"]: item for item in file_sources if item.get("id")}.values()),
        "image_generations": image_generations,
    }
    run.plan = [
        {
            "id": node_ids[index],
            "title": str(node.get("title") or "Шаг"),
            "type": str(node.get("type") or "llm"),
        }
        for index, node in enumerate(nodes)
    ]
    run.finished_at = timezone.now()
    run.save(update_fields=["state", "output_payload", "plan", "finished_at", "updated_at"])
    return run


def execute_graph_run_v2(run_id):
    with transaction.atomic():
        run = (
            AgentRun.objects.select_for_update()
            .select_related("owner", "agent", "project")
            .prefetch_related("steps", "approvals")
            .get(pk=run_id)
        )
        if run.state != AgentRun.State.QUEUED:
            return run
        agent = run.agent
        if agent is None:
            return _fail(run, None, "agent_missing", "Для graph runtime требуется одиночный агент")
        run.state = AgentRun.State.PLANNING
        run.started_at = run.started_at or timezone.now()
        run.finished_at = None
        run.save(update_fields=["state", "started_at", "finished_at", "updated_at"])

    nodes, node_ids, by_id, position, outgoing = _graph_parts(agent)
    if not nodes:
        return _fail(run, None, "graph_empty", "Карта действий агента пуста")
    if len(nodes) > agent.max_steps:
        return _fail(run, None, "graph_step_limit", f"Карта содержит {len(nodes)} шагов при лимите {agent.max_steps}")
    if len(by_id) != len(nodes):
        return _fail(run, None, "graph_duplicate_node", "Карта содержит повторяющиеся ID шагов")

    current = node_ids[0]
    route = []
    traversed = 0
    sequence = 0
    while current:
        traversed += 1
        if traversed > agent.max_steps:
            return _fail(run, None, "graph_cycle_detected", "Workflow превысил лимит переходов. Возможен цикл в карте действий.")
        if current not in by_id:
            return _fail(run, None, "graph_target_missing", f"Маршрут ведёт к отсутствующему шагу: {current}")
        route.append(current)
        node = by_id[current]
        sequence += 1

        run.refresh_from_db(fields=["state", "cost_actual_rub", "step_count", "tool_call_count", "started_at", "input_payload"])
        if run.state == AgentRun.State.CANCELED:
            return run
        if run.started_at and (timezone.now() - run.started_at).total_seconds() > agent.max_runtime_seconds:
            return _fail(run, None, "agent_runtime_timeout", f"Достигнут лимит времени запуска: {agent.max_runtime_seconds} секунд")

        node_type = str(node.get("type") or "llm").strip().lower()
        default_next = _default_next(current, node_ids, position, outgoing)

        if node_type == "condition":
            next_node = _run_condition(run, agent, node, sequence, node_ids, position, outgoing)
            if next_node == "__terminal__":
                return run
            current = next_node
            continue
        if node_type == "notify":
            _run_notify(run, agent, node, sequence)
            current = default_next
            continue
        if node_type == "wait":
            if handle_wait_node(run, agent, node, sequence):
                return run
            current = default_next
            continue
        if node_type == "finish":
            _run_finish(run, agent, node, sequence)
            break

        already_done = run.steps.filter(
            node_id=current,
            state__in=[AgentStepRun.State.COMPLETED, AgentStepRun.State.SKIPPED],
        ).exists()
        if already_done:
            current = default_next
            continue

        needed_tools = _planned_tool_calls(agent, node_type)
        if run.tool_call_count + needed_tools > agent.max_tool_calls:
            return _fail(run, None, "agent_tool_limit_exceeded", f"Достигнут лимит вызовов инструментов: {agent.max_tool_calls}")

        remaining_budget, snapshot = effective_remaining_budget(agent, run=run)
        if remaining_budget <= 0 and node_type in SUPPORTED_LLM_NODES | {"image"}:
            message = (
                "Лимит расходов агента исчерпан. "
                f"За запуск: {snapshot['run_spend']}/{snapshot['run_limit']} ₽; "
                f"сегодня: {snapshot['day_spend']}/{snapshot['day_limit']} ₽; "
                f"за месяц: {snapshot['month_spend']}/{snapshot['month_limit']} ₽."
            )
            return _budget_exceeded(run, message, code="agent_period_budget_exceeded")

        if node_type == "approval":
            if _handle_approval(run, agent, node, sequence):
                return run
            current = default_next
            continue
        if node_type == "image":
            terminal = _run_image_node(run, agent, node, sequence, remaining_budget)
            if terminal is not None:
                return terminal
            current = default_next
            continue
        if node_type in UNSUPPORTED_EXTERNAL_NODES:
            _skip_external(run, agent, node, sequence)
            current = default_next
            continue
        if node_type not in SUPPORTED_LLM_NODES:
            _skip_external(run, agent, node, sequence)
            current = default_next
            continue
        terminal = _run_llm_node(run, agent, node, sequence, remaining_budget)
        if terminal is not None:
            return terminal
        current = default_next

    return _finish_run(run, nodes, node_ids, route)
