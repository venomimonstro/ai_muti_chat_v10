from django.db.models import Q

from apps.memory_store.models import MemoryItem

MAX_AGENT_MEMORY_ITEMS = 12
MAX_AGENT_MEMORY_CHARS = 12000


def memory_items_for_agent(agent):
    policy = agent.memory_policy or {}
    if policy.get("enabled", True) is False:
        return []
    queryset = MemoryItem.objects.filter(
        owner=agent.owner,
        status=MemoryItem.Status.ACTIVE,
        enabled=True,
    )
    scopes = Q(scope=MemoryItem.Scope.GLOBAL)
    if agent.project_id and policy.get("project", True) is not False:
        scopes |= Q(scope=MemoryItem.Scope.PROJECT, project_id=agent.project_id)
    queryset = queryset.filter(scopes).order_by("-pinned", "-importance_score", "-updated_at")
    return list(queryset[:MAX_AGENT_MEMORY_ITEMS])


def memory_context_for_agent(agent):
    items = memory_items_for_agent(agent)
    if not items:
        return "", []
    blocks = []
    refs = []
    total = 0
    for item in items:
        text = str(item.content or "").strip()
        if not text:
            continue
        remaining = MAX_AGENT_MEMORY_CHARS - total
        if remaining <= 0:
            break
        text = text[:remaining]
        total += len(text)
        blocks.append(f"- [{item.memory_type}] {text}")
        refs.append(str(item.id))
    if not blocks:
        return "", []
    return "\n".join(blocks), refs


def memory_snapshot_for_run(run, agent):
    """Return a stable memory snapshot for this agent within one run.

    Agent memory remains editable while work is running, but those edits are
    intentionally visible only to subsequent runs. Visual workflows and team
    runtimes may call this helper many times; every call for the same run/agent
    returns the exact same text and references captured on first use.
    """
    payload = dict(run.input_payload or {})
    snapshots = dict(payload.get("memory_snapshots") or {})
    key = str(agent.id)
    existing = snapshots.get(key)
    if isinstance(existing, dict):
        text = str(existing.get("text") or "")
        refs = [str(value) for value in (existing.get("refs") or [])]
        return text, refs

    text, refs = memory_context_for_agent(agent)
    snapshots[key] = {
        "text": text,
        "refs": [str(value) for value in refs],
    }
    payload["memory_snapshots"] = snapshots
    run.input_payload = payload
    run.save(update_fields=["input_payload", "updated_at"])
    return text, refs
