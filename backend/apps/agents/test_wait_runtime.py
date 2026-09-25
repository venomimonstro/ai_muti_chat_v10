from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.accounts.models import User

from .graph_runtime_v2 import execute_graph_run_v2
from .models import Agent, AgentRun, AgentStepRun
from .recovery import recover_agent_run
from .wait_runtime import WAIT_KEY, resume_due_waits


@pytest.mark.django_db
def test_graph_wait_pauses_without_running_following_steps():
    user = User.objects.create_user(username="wait-owner", email="wait-owner@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Wait worker",
        objective="Wait safely",
        status=Agent.Status.ACTIVE,
        graph={
            "nodes": [
                {"id": "wait", "title": "Подождать", "type": "wait", "wait_minutes": 30},
                {"id": "finish", "title": "Завершить", "type": "finish"},
            ],
            "edges": [{"from": "wait", "to": "finish"}],
        },
    )
    run = AgentRun.objects.create(owner=user, agent=agent, objective="Wait", state=AgentRun.State.QUEUED)

    result = execute_graph_run_v2(run.id)

    assert result.state == AgentRun.State.WAITING_TOOL
    result.refresh_from_db()
    assert result.input_payload[WAIT_KEY]["node_id"] == "wait"
    wait_step = result.steps.get(node_id="wait")
    assert wait_step.state == AgentStepRun.State.PENDING
    assert not result.steps.filter(node_id="finish").exists()


@pytest.mark.django_db
def test_due_wait_continues_same_run_and_finishes():
    user = User.objects.create_user(username="wait-resume", email="wait-resume@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Wait worker",
        objective="Wait safely",
        status=Agent.Status.ACTIVE,
        graph={
            "nodes": [
                {"id": "wait", "title": "Подождать", "type": "wait", "wait_minutes": 30},
                {"id": "finish", "title": "Завершить", "type": "finish"},
            ],
            "edges": [{"from": "wait", "to": "finish"}],
        },
    )
    run = AgentRun.objects.create(owner=user, agent=agent, objective="Wait", state=AgentRun.State.QUEUED)
    execute_graph_run_v2(run.id)
    run.refresh_from_db()
    payload = dict(run.input_payload)
    payload[WAIT_KEY]["resume_at"] = (timezone.now() - timedelta(minutes=1)).isoformat()
    run.input_payload = payload
    run.state = AgentRun.State.QUEUED
    run.started_at = timezone.now()
    run.save(update_fields=["input_payload", "state", "started_at", "updated_at"])

    result = execute_graph_run_v2(run.id)

    assert result.id == run.id
    assert result.state == AgentRun.State.COMPLETED
    assert WAIT_KEY not in result.input_payload
    assert result.steps.get(node_id="wait").state == AgentStepRun.State.COMPLETED
    assert result.steps.get(node_id="finish").state == AgentStepRun.State.COMPLETED
    assert result.steps.count() == 2


@pytest.mark.django_db
def test_stale_recovery_does_not_kill_legitimate_wait(monkeypatch):
    user = User.objects.create_user(username="wait-stale", email="wait-stale@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Wait worker", objective="Wait", status=Agent.Status.ACTIVE)
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Wait",
        state=AgentRun.State.WAITING_TOOL,
        input_payload={WAIT_KEY: {"node_id": "wait", "resume_at": (timezone.now() + timedelta(hours=4)).isoformat(), "wait_minutes": 240}},
    )
    AgentRun.objects.filter(pk=run.id).update(updated_at=timezone.now() - timedelta(hours=3))

    recovered = recover_agent_run(run.id)

    assert recovered is False
    run.refresh_from_db()
    assert run.state == AgentRun.State.WAITING_TOOL


@pytest.mark.django_db(transaction=True)
def test_resume_due_wait_requeues_once():
    user = User.objects.create_user(username="wait-dispatch", email="wait-dispatch@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Wait worker", objective="Wait", status=Agent.Status.ACTIVE)
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Wait",
        state=AgentRun.State.WAITING_TOOL,
        input_payload={WAIT_KEY: {"node_id": "wait", "resume_at": (timezone.now() - timedelta(minutes=1)).isoformat(), "wait_minutes": 1}},
        started_at=timezone.now() - timedelta(hours=2),
    )

    with patch("apps.agents.tasks.enqueue_agent_run") as enqueue:
        resumed = resume_due_waits()

    assert resumed == 1
    run.refresh_from_db()
    assert run.state == AgentRun.State.QUEUED
    assert run.started_at > timezone.now() - timedelta(minutes=1)
    enqueue.assert_called_once_with(str(run.id))
