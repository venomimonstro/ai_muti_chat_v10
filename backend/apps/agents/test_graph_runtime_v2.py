import pytest

from apps.accounts.models import Notification, User

from .graph_runtime_v2 import execute_graph_run_v2
from .models import Agent, AgentRun
from .readiness import agent_readiness


@pytest.mark.django_db
def test_condition_routes_to_notify_then_finish_without_llm():
    user = User.objects.create_user(username="graph-v2", email="graph-v2@example.com", password="StrongPass123!")
    graph = {
        "version": 2,
        "nodes": [
            {
                "id": "condition",
                "title": "Проверить задачу",
                "type": "condition",
                "condition_source": "objective",
                "operator": "contains",
                "value": "срочно",
                "on_true": "notify",
                "on_false": "finish",
            },
            {
                "id": "notify",
                "title": "Сообщить пользователю",
                "type": "notify",
                "notification_title": "Срочная задача",
                "message": "Workflow распознал срочную задачу.",
            },
            {"id": "finish", "title": "Готово", "type": "finish"},
        ],
        "edges": [
            {"from": "condition", "to": "notify"},
            {"from": "notify", "to": "finish"},
        ],
    }
    agent = Agent.objects.create(
        owner=user,
        name="Branch worker",
        objective="Route tasks",
        status=Agent.Status.ACTIVE,
        graph=graph,
        max_steps=10,
    )
    assert agent_readiness(agent)["ready"] is True
    run = AgentRun.objects.create(owner=user, agent=agent, objective="Срочно подготовить отчёт", state=AgentRun.State.QUEUED)

    result = execute_graph_run_v2(run.id)

    assert result.state == AgentRun.State.COMPLETED
    assert result.output_payload["route"] == ["condition", "notify", "finish"]
    assert list(result.steps.values_list("action_type", flat=True)) == ["condition", "notify", "finish"]
    notification = Notification.objects.get(user=user, dedupe_key=f"agent-node:{run.id}:notify")
    assert notification.title == "Срочная задача"


@pytest.mark.django_db
def test_condition_false_branch_skips_notify():
    user = User.objects.create_user(username="graph-v2-false", email="graph-v2-false@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Branch worker",
        objective="Route tasks",
        status=Agent.Status.ACTIVE,
        max_steps=10,
        graph={
            "nodes": [
                {"id": "condition", "title": "Check", "type": "condition", "condition_source": "objective", "operator": "contains", "value": "срочно", "on_true": "notify", "on_false": "finish"},
                {"id": "notify", "title": "Notify", "type": "notify"},
                {"id": "finish", "title": "Finish", "type": "finish"},
            ],
            "edges": [{"from": "condition", "to": "notify"}, {"from": "notify", "to": "finish"}],
        },
    )
    run = AgentRun.objects.create(owner=user, agent=agent, objective="Обычная задача", state=AgentRun.State.QUEUED)

    result = execute_graph_run_v2(run.id)

    assert result.state == AgentRun.State.COMPLETED
    assert result.output_payload["route"] == ["condition", "finish"]
    assert Notification.objects.filter(user=user).count() == 0
    assert not result.steps.filter(node_id="notify").exists()


@pytest.mark.django_db
def test_finish_stops_nodes_below_it():
    user = User.objects.create_user(username="graph-finish", email="graph-finish@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Finish worker",
        objective="Finish",
        status=Agent.Status.ACTIVE,
        graph={
            "nodes": [
                {"id": "finish", "title": "Finish", "type": "finish"},
                {"id": "notify", "title": "Must not run", "type": "notify"},
            ],
            "edges": [{"from": "finish", "to": "notify"}],
        },
    )
    run = AgentRun.objects.create(owner=user, agent=agent, objective="Stop", state=AgentRun.State.QUEUED)

    result = execute_graph_run_v2(run.id)

    assert result.state == AgentRun.State.COMPLETED
    assert result.output_payload["route"] == ["finish"]
    assert Notification.objects.filter(user=user).count() == 0


@pytest.mark.django_db
def test_readiness_rejects_missing_condition_target_and_self_loop():
    user = User.objects.create_user(username="graph-invalid", email="graph-invalid@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Broken graph",
        objective="Check",
        status=Agent.Status.ACTIVE,
        graph={
            "nodes": [
                {"id": "condition", "title": "Broken", "type": "condition", "operator": "contains", "value": "x", "on_true": "condition", "on_false": "missing"},
            ],
            "edges": [],
        },
    )

    result = agent_readiness(agent)

    assert result["ready"] is False
    assert any("сама в себя" in item for item in result["blockers"])
    assert any("отсутствующему шагу" in item for item in result["blockers"])
