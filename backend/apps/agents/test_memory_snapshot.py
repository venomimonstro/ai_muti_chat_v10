import pytest

from apps.accounts.models import User
from apps.memory_store.models import MemoryItem

from .memory import memory_snapshot_for_run
from .models import Agent, AgentRun


@pytest.mark.django_db
def test_memory_snapshot_is_stable_within_run_and_refreshes_next_run():
    user = User.objects.create_user(
        username="agent-memory-snapshot",
        email="agent-memory-snapshot@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Memory agent",
        objective="Use stable memory",
        status=Agent.Status.ACTIVE,
    )
    item = MemoryItem.objects.create(
        owner=user,
        scope=MemoryItem.Scope.GLOBAL,
        memory_type=MemoryItem.Type.FACT,
        content="Версия A",
        normalized_content="версия a",
        importance_score="0.90",
        confidence_score="1.00",
        trust_level="1.00",
        status=MemoryItem.Status.ACTIVE,
        pinned=True,
        enabled=True,
    )
    first_run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="First run",
        state=AgentRun.State.QUEUED,
    )

    first_text, first_refs = memory_snapshot_for_run(first_run, agent)
    assert "Версия A" in first_text
    assert str(item.id) in first_refs

    item.content = "Версия B"
    item.normalized_content = "версия b"
    item.save(update_fields=["content", "normalized_content", "updated_at"])

    repeated_text, repeated_refs = memory_snapshot_for_run(first_run, agent)
    assert repeated_text == first_text
    assert repeated_refs == first_refs
    assert "Версия B" not in repeated_text

    second_run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Second run",
        state=AgentRun.State.QUEUED,
    )
    second_text, second_refs = memory_snapshot_for_run(second_run, agent)
    assert "Версия B" in second_text
    assert str(item.id) in second_refs


@pytest.mark.django_db
def test_team_run_keeps_separate_memory_snapshots_per_agent():
    user = User.objects.create_user(
        username="agent-memory-team",
        email="agent-memory-team@example.com",
        password="StrongPass123!",
    )
    first = Agent.objects.create(owner=user, name="First", objective="First", status=Agent.Status.ACTIVE)
    second = Agent.objects.create(owner=user, name="Second", objective="Second", status=Agent.Status.ACTIVE)
    MemoryItem.objects.create(
        owner=user,
        scope=MemoryItem.Scope.GLOBAL,
        memory_type=MemoryItem.Type.FACT,
        content="Общий факт",
        normalized_content="общий факт",
        status=MemoryItem.Status.ACTIVE,
        enabled=True,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=first,
        objective="Snapshot multiple agents",
        state=AgentRun.State.QUEUED,
    )

    memory_snapshot_for_run(run, first)
    memory_snapshot_for_run(run, second)
    run.refresh_from_db()
    snapshots = (run.input_payload or {}).get("memory_snapshots") or {}
    assert str(first.id) in snapshots
    assert str(second.id) in snapshots
