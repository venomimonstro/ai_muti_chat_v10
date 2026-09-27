from __future__ import annotations

from collections import defaultdict, deque

from django.db import transaction
from django.utils import timezone

from .graph_runtime_shared import (
    _execute_node,
    _fail,
    _graph_parts,
    _pause_for_approval,
    _pause_for_wait,
    _resolve_completed_node_ids,
    _save_step,
)
from .models import AgentRun


def _reachable_from(start_id: str, outgoing: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    queue = deque([start_id])
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(outgoing.get(current, []))
    return seen


def _active_node_ids(nodes: list[dict], outgoing: dict[str, list[str]]) -> set[str]:
    all_ids = {str(node.get("id") or "") for node in nodes if node.get("id")}
    incoming: dict[str, int] = defaultdict(int)
    for sources in outgoing.values():
        for target in sources:
            incoming[target] += 1
    roots = [node_id for node_id in all_ids if incoming[node_id] == 0]
    if not roots:
        return all_ids
    active: set[str] = set()
    for root in roots:
        active |= _reachable_from(root, outgoing)
    return active


def _next_ids_for_condition(node: dict, outgoing: dict[str, list[str]], result: dict) -> set[str]:
    node_id = str(node.get("id") or "")
    targets = outgoing.get(node_id, [])
    if not targets:
        return set()
    truthy = bool(result.get("condition"))
    preferred_key = "true_target" if truthy else "false_target"
    configured = node.get(preferred_key)
    if configured and str(configured) in targets:
        return {str(configured)}
    if len(targets) == 1:
        return {targets[0]}
    return {targets[0] if truthy else targets[-1]}


def _finish(run: AgentRun, nodes: list[dict], node_ids: list[str]) -> AgentRun:
    steps = list(run.steps.order_by("created_at", "id"))
    output_payload = dict(run.output_payload or {})
    output_payload["completed_steps"] = [
        {
            "id": step.node_id,
            "title": step.title,
            "type": step.node_type,
            "status": step.status,
            "output": step.output_payload,
        }
        for step in steps
    ]
    image_generations = []
    for step in steps:
        if step.node_type != "image":
            continue
        images = step.output_payload.get("images") if isinstance(step.output_payload, dict) else None
        if images:
            image_generations.extend(images)
    if image_generations:
        output_payload["image_generations"] = image_generations
    run.state = AgentRun.State.COMPLETED
    run.output_payload = output_payload
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
        # PostgreSQL refuses FOR UPDATE when Django also joins nullable FKs
        # (project/team/etc.) through LEFT OUTER JOIN. Lock only AgentRun itself;
        # related rows are read-only context for this execution claim.
        run = (
            AgentRun.objects.select_for_update(of=("self",))
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

    active_ids = _active_node_ids(nodes, outgoing)
    completed_ids = _resolve_completed_node_ids(run)
    skipped_ids = {
        step.node_id
        for step in run.steps.filter(status="skipped")
        if step.node_id
    }
    reachable = set(active_ids)
    if completed_ids or skipped_ids:
        visited = completed_ids | skipped_ids
        frontier: set[str] = set()
        for node_id in visited:
            node = by_id.get(node_id)
            if node and str(node.get("type") or "") == "condition":
                step = run.steps.filter(node_id=node_id).order_by("-created_at").first()
                result = step.output_payload if step else {}
                frontier |= _next_ids_for_condition(node, outgoing, result or {})
            else:
                frontier |= set(outgoing.get(node_id, []))
        if frontier:
            reachable = set(visited)
            queue = deque(frontier)
            while queue:
                current = queue.popleft()
                if current in reachable:
                    continue
                reachable.add(current)
                queue.extend(outgoing.get(current, []))

    run.state = AgentRun.State.RUNNING
    run.save(update_fields=["state", "updated_at"])

    for node_id in node_ids:
        if node_id not in active_ids or node_id not in reachable:
            continue
        if node_id in completed_ids or node_id in skipped_ids:
            continue
        node = by_id[node_id]
        node_type = str(node.get("type") or "llm")
        if node_type == "approval":
            return _pause_for_approval(run, agent, node)
        if node_type == "wait":
            return _pause_for_wait(run, agent, node)
        try:
            result = _execute_node(run, agent, node)
        except Exception as exc:
            return _fail(run, node, "node_failed", str(exc))
        step = _save_step(run, node, result)
        completed_ids.add(node_id)
        if node_type == "condition":
            allowed = _next_ids_for_condition(node, outgoing, result)
            for candidate in outgoing.get(node_id, []):
                if candidate not in allowed:
                    skipped_ids.add(candidate)
                    skip_node = by_id.get(candidate)
                    if skip_node:
                        _save_step(
                            run,
                            skip_node,
                            {"reason": "condition_branch_not_selected"},
                            status="skipped",
                        )
            reachable |= allowed
        if node_type == "finish":
            break

    return _finish(run, nodes, node_ids)
