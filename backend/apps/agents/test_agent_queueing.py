from unittest.mock import patch

import pytest

from apps.accounts.models import User

from .models import Agent, AgentRun
from .tasks import enqueue_agent_run


@pytest.mark.django_db
def test_enqueue_agent_run_marks_run_failed_when_broker_is_unavailable():
    user = User.objects.create_user(
        username="queue-failure",
        email="queue-failure@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Queue test agent",
        objective="Test queue failure",
        status=Agent.Status.ACTIVE,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Queue me",
        state=AgentRun.State.QUEUED,
        input_payload={"trigger": "schedule", "schedule_id": "00000000-0000-0000-0000-000000000001"},
    )

    with patch("apps.agents.tasks.execute_agent_run_task.delay", side_effect=RuntimeError("redis unavailable")):
        queued = enqueue_agent_run(run.id)

    assert queued is False
    run.refresh_from_db()
    assert run.state == AgentRun.State.FAILED
    assert run.error_code == "queue_unavailable"
    assert "redis unavailable" in run.error_message
    assert run.finished_at is not None


@pytest.mark.django_db
def test_enqueue_agent_run_does_not_overwrite_nonqueued_run_on_late_broker_error():
    user = User.objects.create_user(
        username="queue-race",
        email="queue-race@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Race agent",
        objective="Protect terminal state",
        status=Agent.Status.ACTIVE,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Already canceled",
        state=AgentRun.State.CANCELED,
    )

    with patch("apps.agents.tasks.execute_agent_run_task.delay", side_effect=RuntimeError("redis unavailable")):
        queued = enqueue_agent_run(run.id)

    assert queued is False
    run.refresh_from_db()
    assert run.state == AgentRun.State.CANCELED
    assert run.error_code == ""
