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
