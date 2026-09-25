from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import Notification, User

from .models import Agent, AgentApproval, AgentRun, AgentStepRun
from .tasks import expire_stale_agent_approvals


def _waiting_run(*, username="approval-expiry"):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Autonomous publisher",
        objective="Prepare and publish content safely",
        status=Agent.Status.ACTIVE,
        autonomy=Agent.Autonomy.SEMI_AUTONOMOUS,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Prepare article",
        input_payload={"trigger": "schedule"},
        state=AgentRun.State.WAITING_APPROVAL,
    )
    step = AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=1,
        node_id="approve-publish",
        title="Approve publish",
        action_type="approval",
        state=AgentStepRun.State.WAITING_APPROVAL,
        started_at=timezone.now(),
    )
    approval = AgentApproval.objects.create(
        run=run,
        step=step,
        requested_by_agent=agent,
        title="Разрешить публикацию",
        description="Protected external action",
        action_payload={"kind": "workflow_approval", "node_id": "approve-publish"},
    )
    return user, agent, run, step, approval


@pytest.mark.django_db(transaction=True)
@override_settings(AGENT_APPROVAL_TIMEOUT_HOURS=72)
def test_old_approval_expires_and_cancels_waiting_run():
    user, _agent, run, step, approval = _waiting_run()
    AgentApproval.objects.filter(pk=approval.pk).update(
        created_at=timezone.now() - timedelta(hours=73)
    )

    result = expire_stale_agent_approvals.run()

    assert result["expired"] == 1
    assert result["canceled_runs"] == 1
    approval.refresh_from_db()
    step.refresh_from_db()
    run.refresh_from_db()
    assert approval.status == AgentApproval.Status.EXPIRED
    assert approval.decided_at is not None
    assert step.state == AgentStepRun.State.SKIPPED
    assert run.state == AgentRun.State.CANCELED
    assert run.error_code == "agent_approval_expired"
    assert run.finished_at is not None
    assert Notification.objects.filter(
        user=user,
        dedupe_key=f"agent-run:{run.id}:{AgentRun.State.CANCELED}",
    ).exists()


@pytest.mark.django_db(transaction=True)
@override_settings(AGENT_APPROVAL_TIMEOUT_HOURS=72)
def test_recent_approval_is_left_untouched():
    _user, _agent, run, step, approval = _waiting_run(username="approval-recent")

    result = expire_stale_agent_approvals.run()

    assert result["expired"] == 0
    approval.refresh_from_db()
    step.refresh_from_db()
    run.refresh_from_db()
    assert approval.status == AgentApproval.Status.PENDING
    assert step.state == AgentStepRun.State.WAITING_APPROVAL
    assert run.state == AgentRun.State.WAITING_APPROVAL


@pytest.mark.django_db(transaction=True)
@override_settings(AGENT_APPROVAL_TIMEOUT_HOURS=72)
def test_expiring_one_of_multiple_approvals_keeps_run_waiting():
    _user, agent, run, step, old = _waiting_run(username="approval-multiple")
    AgentApproval.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(hours=73)
    )
    second_step = AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=2,
        node_id="approve-second",
        title="Second approval",
        action_type="approval",
        state=AgentStepRun.State.WAITING_APPROVAL,
        started_at=timezone.now(),
    )
    current = AgentApproval.objects.create(
        run=run,
        step=second_step,
        requested_by_agent=agent,
        title="Второе подтверждение",
        action_payload={"kind": "workflow_approval", "node_id": "approve-second"},
    )

    result = expire_stale_agent_approvals.run()

    assert result["expired"] == 1
    assert result["canceled_runs"] == 0
    old.refresh_from_db()
    current.refresh_from_db()
    run.refresh_from_db()
    assert old.status == AgentApproval.Status.EXPIRED
    assert current.status == AgentApproval.Status.PENDING
    assert run.state == AgentRun.State.WAITING_APPROVAL
