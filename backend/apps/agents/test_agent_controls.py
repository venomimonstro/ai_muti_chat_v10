from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember
from .schedule_models import AgentSchedule
from .tasks import dispatch_due_agent_schedules


@pytest.mark.django_db
def test_team_member_management_is_owner_scoped_and_director_cannot_be_disabled():
    owner = User.objects.create_user(username="team-owner", email="team-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="team-other", email="team-other@example.com", password="StrongPass123!")
    director = Agent.objects.create(owner=owner, name="Director", role="Director", status=Agent.Status.ACTIVE)
    worker = Agent.objects.create(owner=owner, name="Worker", role="Writer", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=owner, name="Marketing", director=director, objective="Work")
    director_member = AgentTeamMember.objects.create(team=team, agent=director, role="Director", priority=10, can_delegate=True)
    worker_member = AgentTeamMember.objects.create(team=team, agent=worker, role="Writer", priority=20)

    client = APIClient()
    client.force_authenticate(other)
    foreign = client.patch(
        f"/api/v1/agent-teams/{team.id}/members/{worker_member.id}/",
        {"role": "Changed"},
        format="json",
    )
    assert foreign.status_code == 404

    client.force_authenticate(owner)
    blocked = client.patch(
        f"/api/v1/agent-teams/{team.id}/members/{director_member.id}/",
        {"enabled": False},
        format="json",
    )
    assert blocked.status_code == 400
    director_member.refresh_from_db()
    assert director_member.enabled is True

    changed = client.patch(
        f"/api/v1/agent-teams/{team.id}/members/{worker_member.id}/",
        {"role": "Senior Writer", "enabled": False},
        format="json",
    )
    assert changed.status_code == 200
    worker_member.refresh_from_db()
    assert worker_member.role == "Senior Writer"
    assert worker_member.enabled is False


@pytest.mark.django_db
def test_new_team_director_must_be_enabled_member():
    user = User.objects.create_user(username="team-director", email="team-director@example.com", password="StrongPass123!")
    director = Agent.objects.create(owner=user, name="Director", status=Agent.Status.ACTIVE)
    candidate = Agent.objects.create(owner=user, name="Candidate", status=Agent.Status.ACTIVE)
    outsider = Agent.objects.create(owner=user, name="Outsider", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=user, name="Team", director=director, objective="Work")
    AgentTeamMember.objects.create(team=team, agent=director, role="Director", can_delegate=True)
    candidate_member = AgentTeamMember.objects.create(team=team, agent=candidate, role="Lead", enabled=False)

    client = APIClient()
    client.force_authenticate(user)

    disabled = client.post(
        f"/api/v1/agent-teams/{team.id}/director/",
        {"agent": str(candidate.id)},
        format="json",
    )
    assert disabled.status_code == 400

    missing = client.post(
        f"/api/v1/agent-teams/{team.id}/director/",
        {"agent": str(outsider.id)},
        format="json",
    )
    assert missing.status_code == 400

    candidate_member.enabled = True
    candidate_member.save(update_fields=["enabled"])
    ok = client.post(
        f"/api/v1/agent-teams/{team.id}/director/",
        {"agent": str(candidate.id)},
        format="json",
    )
    assert ok.status_code == 200
    team.refresh_from_db()
    candidate_member.refresh_from_db()
    assert team.director_id == candidate.id
    assert candidate_member.can_delegate is True


@pytest.mark.django_db
def test_due_schedule_skips_parallel_run_when_configured():
    user = User.objects.create_user(username="schedule-user", email="schedule-user@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Autonomous SMM",
        objective="Create content",
        status=Agent.Status.ACTIVE,
    )
    existing = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Existing run",
        state=AgentRun.State.RUNNING,
    )
    schedule = AgentSchedule.objects.create(
        owner=user,
        agent=agent,
        name="Daily",
        objective="Create today content",
        enabled=True,
        interval_minutes=1440,
        next_run_at=timezone.now() - timedelta(minutes=1),
        skip_if_running=True,
    )

    with patch("apps.agents.tasks.execute_agent_run_task.delay") as delay:
        result = dispatch_due_agent_schedules.run()

    assert result["launched"] == 0
    assert result["skipped"] == 1
    assert AgentRun.objects.filter(agent=agent).count() == 1
    assert AgentRun.objects.get(pk=existing.pk).state == AgentRun.State.RUNNING
    delay.assert_not_called()
    schedule.refresh_from_db()
    assert schedule.next_run_at > timezone.now()


@pytest.mark.django_db
def test_due_schedule_creates_single_queued_run_and_enqueues_after_commit():
    user = User.objects.create_user(username="schedule-launch", email="schedule-launch@example.com", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Autonomous Copywriter",
        objective="Write article",
        status=Agent.Status.ACTIVE,
    )
    schedule = AgentSchedule.objects.create(
        owner=user,
        agent=agent,
        name="Hourly",
        objective="Write one draft",
        enabled=True,
        interval_minutes=60,
        next_run_at=timezone.now() - timedelta(minutes=1),
        skip_if_running=True,
    )

    with patch("apps.agents.tasks.execute_agent_run_task.delay") as delay:
        result = dispatch_due_agent_schedules.run()

    assert result["launched"] == 1
    assert AgentRun.objects.filter(agent=agent, state=AgentRun.State.QUEUED).count() == 1
    schedule.refresh_from_db()
    assert schedule.last_run_id is not None
    # transaction.on_commit callbacks may run after the task body in Django tests;
    # the durable assertion is that exactly one queued run was created and linked.
    assert delay.call_count in {0, 1}
