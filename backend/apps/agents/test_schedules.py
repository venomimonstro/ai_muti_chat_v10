from datetime import datetime, time, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun
from .schedule_models import AgentSchedule
from .tasks import dispatch_due_agent_schedules


@pytest.mark.django_db
def test_schedule_is_owner_scoped_and_server_derives_next_run():
    owner = User.objects.create_user(username="schedule-owner", email="schedule-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="schedule-other", email="schedule-other@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=owner, name="Owner agent", objective="Do work", status=Agent.Status.ACTIVE)
    foreign = Agent.objects.create(owner=other, name="Foreign agent", objective="Private", status=Agent.Status.ACTIVE)
    client = APIClient()
    client.force_authenticate(owner)

    malicious_next = (timezone.now() - timedelta(days=30)).isoformat()
    created = client.post(
        "/api/v1/agent-schedules/",
        {
            "agent": str(agent.id),
            "name": "Каждый день",
            "cadence": "daily",
            "local_time": "09:00:00",
            "timezone_name": "Europe/Moscow",
            "next_run_at": malicious_next,
        },
        format="json",
    )
    assert created.status_code == 201
    schedule = AgentSchedule.objects.get(owner=owner)
    assert schedule.next_run_at > timezone.now()

    rejected = client.post(
        "/api/v1/agent-schedules/",
        {"agent": str(foreign.id), "name": "Чужой", "cadence": "interval", "interval_minutes": 1440},
        format="json",
    )
    assert rejected.status_code == 400
    assert AgentSchedule.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_daily_schedule_computes_next_local_time():
    user = User.objects.create_user(username="schedule-daily", email="schedule-daily@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Daily agent", objective="Work", status=Agent.Status.ACTIVE)
    schedule = AgentSchedule(
        owner=user,
        agent=agent,
        name="Morning",
        cadence=AgentSchedule.Cadence.DAILY,
        local_time=time(9, 0),
        timezone_name="Europe/Moscow",
        next_run_at=timezone.now(),
    )
    after = datetime(2026, 9, 25, 5, 30, tzinfo=dt_timezone.utc)  # 08:30 Moscow
    next_run = schedule.compute_next_run(after=after)
    assert next_run == datetime(2026, 9, 25, 6, 0, tzinfo=dt_timezone.utc)


@pytest.mark.django_db
def test_weekday_schedule_skips_weekend():
    user = User.objects.create_user(username="schedule-weekday", email="schedule-weekday@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Weekday agent", objective="Work", status=Agent.Status.ACTIVE)
    schedule = AgentSchedule(
        owner=user,
        agent=agent,
        name="Weekdays",
        cadence=AgentSchedule.Cadence.WEEKDAYS,
        local_time=time(9, 0),
        timezone_name="Europe/Moscow",
        next_run_at=timezone.now(),
    )
    # Friday 25 Sep 2026, already after 09:00 Moscow -> Monday 28 Sep 09:00 Moscow.
    after = datetime(2026, 9, 25, 10, 0, tzinfo=dt_timezone.utc)
    next_run = schedule.compute_next_run(after=after)
    assert next_run == datetime(2026, 9, 28, 6, 0, tzinfo=dt_timezone.utc)


@pytest.mark.django_db
def test_due_schedule_creates_one_run_and_advances_next_run():
    user = User.objects.create_user(username="schedule-due", email="schedule-due@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Daily SMM", objective="Prepare daily post", status=Agent.Status.ACTIVE)
    schedule = AgentSchedule.objects.create(
        owner=user,
        agent=agent,
        name="Daily",
        objective="Prepare today's post",
        cadence=AgentSchedule.Cadence.INTERVAL,
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
    assert schedule.last_run_at is not None
    assert schedule.next_run_at > timezone.now()
    delay.assert_called_once_with(str(run.id))


@pytest.mark.django_db
def test_schedule_skip_does_not_fake_last_run_timestamp():
    user = User.objects.create_user(username="schedule-overlap", email="schedule-overlap@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Busy agent", objective="Work", status=Agent.Status.ACTIVE)
    AgentRun.objects.create(owner=user, agent=agent, objective="Existing", state=AgentRun.State.RUNNING)
    schedule = AgentSchedule.objects.create(
        owner=user,
        agent=agent,
        name="No overlap",
        cadence=AgentSchedule.Cadence.INTERVAL,
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
    assert schedule.last_run_at is None
    assert schedule.last_run_id is None
    delay.assert_not_called()
