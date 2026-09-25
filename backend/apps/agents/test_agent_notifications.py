import pytest

from apps.accounts.models import Notification, User

from .models import Agent, AgentRun


@pytest.mark.django_db
def test_scheduled_completed_run_creates_one_notification():
    user = User.objects.create_user(
        username="agent-notify-complete",
        email="agent-notify-complete@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Content Agent",
        objective="Prepare content",
        status=Agent.Status.ACTIVE,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Prepare today's content",
        input_payload={"trigger": "schedule", "schedule_id": "test"},
        state=AgentRun.State.RUNNING,
    )

    run.state = AgentRun.State.COMPLETED
    run.save(update_fields=["state", "updated_at"])
    run.save(update_fields=["updated_at"])

    notifications = Notification.objects.filter(user=user)
    assert notifications.count() == 1
    item = notifications.get()
    assert item.level == Notification.Level.SUCCESS
    assert item.action_url == f"/app/runs/{run.id}"
    assert "Content Agent" in item.body


@pytest.mark.django_db
def test_scheduled_waiting_approval_notifies_user():
    user = User.objects.create_user(
        username="agent-notify-approval",
        email="agent-notify-approval@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Controlled Agent",
        objective="Wait for approval",
        status=Agent.Status.ACTIVE,
        autonomy=Agent.Autonomy.CONTROLLED,
    )

    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Prepare safe action",
        input_payload={"trigger": "schedule", "schedule_id": "test"},
        state=AgentRun.State.WAITING_APPROVAL,
    )

    item = Notification.objects.get(user=user)
    assert item.level == Notification.Level.WARNING
    assert item.action_url == f"/app/runs/{run.id}"
    assert "подтверждения" in item.title.lower()


@pytest.mark.django_db
def test_manual_agent_run_does_not_create_autonomy_notification():
    user = User.objects.create_user(
        username="agent-notify-manual",
        email="agent-notify-manual@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Manual Agent",
        objective="Manual work",
        status=Agent.Status.ACTIVE,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Manual work",
        state=AgentRun.State.RUNNING,
    )
    run.state = AgentRun.State.COMPLETED
    run.save(update_fields=["state", "updated_at"])

    assert Notification.objects.filter(user=user).count() == 0
