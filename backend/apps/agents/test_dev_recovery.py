from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.projects.models import Project

from .dev_recovery import recover_stale_dev_run, stale_dev_runs
from .models import Agent, AgentRun, AgentStepRun, AgentTeam


@pytest.fixture
def stale_dev_run(db):
    user = User.objects.create_user(username="dev-recovery", password="StrongPass123!")
    project = Project.objects.create(owner=user, name="Recovery project")
    director = Agent.objects.create(
        owner=user,
        project=project,
        name="Engineering Director",
        role="Engineering Director",
        objective="Recover safely",
        status=Agent.Status.ACTIVE,
    )
    team = AgentTeam.objects.create(
        owner=user,
        project=project,
        name="Recovery Dev Team",
        objective="Recover safely",
        kind=AgentTeam.Kind.DEVELOPMENT,
        director=director,
    )
    run = AgentRun.objects.create(
        owner=user,
        team=team,
        project=project,
        objective="Long task",
        state=AgentRun.State.RUNNING,
        input_payload={"phase": "validating_changes", "workspace_id": "devrun-12345678", "working_branch": "ai-workspace/run-123"},
        started_at=timezone.now() - timedelta(hours=6),
    )
    AgentStepRun.objects.create(
        run=run,
        agent=director,
        sequence=1,
        node_id="dev-task-one",
        title="Development",
        action_type="llm",
        state=AgentStepRun.State.RUNNING,
        started_at=timezone.now() - timedelta(hours=6),
    )
    AgentRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=5))
    run.refresh_from_db()
    return run


@pytest.mark.django_db
def test_recovery_marks_stale_dev_run_terminal_without_replaying(stale_dev_run):
    with patch("apps.agents.dev_recovery.destroy_workspace") as destroy:
        result = recover_stale_dev_run(stale_dev_run.id, older_than_seconds=3600)

    assert result["recovered"] is True
    stale_dev_run.refresh_from_db()
    assert stale_dev_run.state == AgentRun.State.FAILED
    assert stale_dev_run.error_code == "dev_runtime_interrupted"
    assert stale_dev_run.input_payload["phase"] == "recovery_required"
    assert stale_dev_run.input_payload["recovery"]["working_branch"] == "ai-workspace/run-123"
    step = stale_dev_run.steps.get(sequence=1)
    assert step.state == AgentStepRun.State.FAILED
    destroy.assert_called_once_with(workspace_id="devrun-12345678")


@pytest.mark.django_db
def test_waiting_approval_is_not_auto_recovered(stale_dev_run):
    AgentRun.objects.filter(pk=stale_dev_run.pk).update(
        state=AgentRun.State.WAITING_APPROVAL,
        updated_at=timezone.now() - timedelta(days=2),
    )
    stale_dev_run.refresh_from_db()

    assert list(stale_dev_runs(older_than_seconds=3600).filter(pk=stale_dev_run.pk)) == []
    result = recover_stale_dev_run(stale_dev_run.id, older_than_seconds=3600)
    assert result == {
        "run_id": str(stale_dev_run.id),
        "recovered": False,
        "reason": "state_not_recoverable",
    }
