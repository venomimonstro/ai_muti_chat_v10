from django.db import transaction
from django.db.models import Max

from .models import Agent, AgentVersion


SNAPSHOT_FIELDS = (
    "name",
    "role",
    "objective",
    "instructions",
    "autonomy",
    "status",
    "system_level",
    "tool_policy",
    "graph",
    "memory_policy",
    "max_cost_rub_per_run",
    "max_cost_rub_per_day",
    "max_cost_rub_per_month",
    "max_steps",
    "max_tool_calls",
    "max_handoffs",
    "max_retries_per_step",
    "max_runtime_seconds",
)


def agent_snapshot(agent: Agent) -> dict:
    result = {}
    for field in SNAPSHOT_FIELDS:
        value = getattr(agent, field)
        if hasattr(value, "as_tuple"):
            value = str(value)
        result[field] = value
    result["project"] = str(agent.project_id) if agent.project_id else None
    return result


@transaction.atomic
def create_agent_version(agent: Agent, user) -> AgentVersion:
    current = (
        AgentVersion.objects.select_for_update()
        .filter(agent=agent)
        .aggregate(value=Max("version"))["value"]
        or 0
    )
    return AgentVersion.objects.create(
        agent=agent,
        version=int(current) + 1,
        snapshot=agent_snapshot(agent),
        created_by=user,
    )


@transaction.atomic
def restore_agent_version(agent: Agent, version: AgentVersion, user) -> Agent:
    if version.agent_id != agent.id:
        raise ValueError("Version does not belong to agent")
    create_agent_version(agent, user)
    snapshot = dict(version.snapshot or {})
    for field in SNAPSHOT_FIELDS:
        if field in snapshot:
            setattr(agent, field, snapshot[field])
    agent.full_clean(exclude=["project"])
    agent.save(update_fields=[*SNAPSHOT_FIELDS, "updated_at"])
    return agent
