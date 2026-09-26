from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Notification, User

from .graph_runtime import _messages
from .graph_runtime_v2 import _run_condition, _run_notify
from .models import Agent, AgentRun, AgentStepRun
from .wait_runtime import WAIT_KEY, handle_wait_node, wait_is_due


def _subject(username="graph-owner"):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Контент-агент",
        role="SMM specialist",
        objective="Готовить качественный контент",
        instructions="Не придумывать факты",
        status=Agent.Status.ACTIVE,
        graph={"nodes": [{"id": "one", "title": "Шаг", "type": "llm"}], "edges": []},
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Подготовь материал о продукте",
        state=AgentRun.State.QUEUED,
    )
    return user, agent, run


@pytest.mark.django_db
def test_node_prompt_is_part_of_llm_instruction():
    _, agent, run = _subject("graph-prompt")
    marker = "Сравни три оффера и выбери самый понятный владельцу бизнеса"
    messages = _messages(
        run,
        agent,
        {"id": "research", "title": "Выбрать оффер", "type": "research", "prompt": marker},
    )

    assert marker in messages[1]["content"]
    assert "Инструкция этого шага" in messages[1]["content"]


@pytest.mark.django_db
def test_condition_rejects_backward_route_before_loop():
    _, agent, run = _subject("graph-backward")
    run.objective = "да, продолжай"
    run.save(update_fields=["objective"])
    node = {
        "id": "check",
        "title": "Проверить решение",
        "type": "condition",
        "condition_source": "objective",
        "operator": "contains",
        "value": "да",
        "on_true": "start",
        "on_false": "finish",
    }

    target = _run_condition(
        run,
        agent,
        node,
        1,
        ["start", "check", "finish"],
        {"start": 0, "check": 1, "finish": 2},
        {"start": ["check"], "check": ["finish"]},
    )

    run.refresh_from_db()
    assert target == "__terminal__"
    assert run.state == AgentRun.State.FAILED
    assert run.error_code == "graph_condition_backward_jump"
    assert not AgentStepRun.objects.filter(run=run, node_id="check").exists()


@pytest.mark.django_db
def test_wait_node_is_durable_and_completes_after_resume_time():
    _, agent, run = _subject("graph-wait")
    node = {"id": "pause", "title": "Подождать ответ", "type": "wait", "wait_minutes": 30}

    waiting = handle_wait_node(run, agent, node, 1)
    run.refresh_from_db()
    step = AgentStepRun.objects.get(run=run, node_id="pause")

    assert waiting is True
    assert run.state == AgentRun.State.WAITING_TOOL
    assert step.state == AgentStepRun.State.PENDING
    assert WAIT_KEY in run.input_payload
    assert wait_is_due(run) is False

    payload = dict(run.input_payload)
    payload[WAIT_KEY] = {
        **payload[WAIT_KEY],
        "resume_at": (timezone.now() - timedelta(seconds=5)).isoformat(),
    }
    run.input_payload = payload
    run.state = AgentRun.State.QUEUED
    run.save(update_fields=["input_payload", "state", "updated_at"])

    waiting_again = handle_wait_node(run, agent, node, 1)
    run.refresh_from_db()
    step.refresh_from_db()

    assert waiting_again is False
    assert step.state == AgentStepRun.State.COMPLETED
    assert WAIT_KEY not in run.input_payload
    assert run.state == AgentRun.State.PLANNING
    assert AgentStepRun.objects.filter(run=run, node_id="pause").count() == 1


@pytest.mark.django_db
def test_notify_node_is_idempotent_on_retry():
    user, agent, run = _subject("graph-notify")
    node = {
        "id": "notify-owner",
        "title": "Сообщить владельцу",
        "type": "notify",
        "notification_title": "Материал готов",
        "message": "Проверьте результат агента.",
    }

    _run_notify(run, agent, node, 1)
    _run_notify(run, agent, node, 2)

    assert Notification.objects.filter(user=user, dedupe_key=f"agent-node:{run.id}:notify-owner").count() == 1
    assert AgentStepRun.objects.filter(run=run, node_id="notify-owner").count() == 1
