from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun
from .schedule_models import AgentSchedule
from .tasks import dispatch_due_agent_schedules


@pytest.mark.django_db
def test_schedule_is_owner_scoped_and_rejects_foreign_agent():
    owner = User.objects.create_user(username="schedule-owner", email="schedule-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="schedule-other", email="schedule-other@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Owner agent", objective="Do work", status=Agent.Status.ACTIVE)
    foreign = Agent.objects.create(owner=other, name="Foreign agent", objective="Private", status=Agent.Status.ACTIVE)
    client = APIClient()
    client.force_authenticate(owner)

    created = client.post(
        "/api/v1/agent-schedules/",
        {"agent": str(agent.id), "name": "Каждый день", "interval_minutes": 1440, "next_run_at": (timezone.now()+timedelta(hours=1)).isoformat()},
        format="json",
    )
    assert created.status_code == 201

    rejected = client.post(
        "/api/v1/agent-schedules/",
        {"agent": str(foreign.id), "name": "Чужой", "interval_minutes": 1440, "next_run_at": (timezone.now()+timedelta(hours=1)).isoformat()},
        format="json",
    )
    assert rejected.status_code == 400
    assert AgentSchedule.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_due_schedule_creates_one_run_and_advances_next_run():
    user = User.objects.create_user(username="schedule-due", email="schedule-due@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Daily SMM", objective="Prepare daily post", status=Agent.Status.ACTIVE)
    schedule = AgentSchedule.objects.create(
        owner=user,
        agent=agent,
        name="Daily",
        objective="Prepare today's post",
        interval_minutes=60,
        next_run_at=timezone.now()-timedelta(minutes=1),
    )

    with patch("apps.agents.tasks.execute_agent_run_task.delay") as delay:
        result = dispatch_due_agent_schedules.run()

    assert result["launched"] == 1
    run = AgentRun.objects.get(agent=agent)
    assert run.input_payload["trigger"] == "schedule"
    schedule.refresh_from_db()
    assert schedule.last_run_id == run.id
    assert schedule.next_run_at > timezone.now()
    delay.assert_called_once_with(str(run.id))


@pytest.mark.django_db
def test_schedule_skips_when_same_agent_is_already_running():
    user = User.objects.create_user(username="schedule-overlap", email="schedule-overlap@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Busy agent", objective="Work", status=Agent.Status.ACTIVE)
    AgentRun.objects.create(owner=user, agent=agent, objective="Existing", state=AgentRun.State.RUNNING)
    schedule = AgentSchedule.objects.create(
        owner=user,
        agent=agent,
        name="No overlap",
        interval_minutes=30,
        next_run_at=timezone.now()-timedelta(minutes=1),
        skip_if_running=True,
    )

    with patch("apps.agents.tasks.execute_agent_run_task.delay") as delay:
        result = dispatch_due_agent_schedules.run()

    assert result["launched"] == 0
    assert result["skipped"] == 1
    assert AgentRun.objects.filter(agent=agent).count() == 1
    schedule.refresh_from_db()
    assert schedule.next_run_at > timezone.now()
    delay.assert_not_called()
