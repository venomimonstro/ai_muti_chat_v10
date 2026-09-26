from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve

from .models import Agent, AgentApproval, AgentRun, AgentStepRun
from .recovery import expire_stale_agent_approvals, recover_stale_agent_runs


@pytest.mark.django_db(transaction=True)
def test_stale_agent_run_releases_all_customer_reserve_formats_and_fails_run(monkeypatch):
    monkeypatch.setenv("AGENT_STALE_TIMEOUT_SECONDS", "1200")
    user = get_user_model().objects.create_user(
        username="agent-recovery",
        email="agent-recovery@example.test",
        password="test-password",
    )
    credit(user, Decimal("20.00"), "test", "agent-recovery-credit")
    agent = Agent.objects.create(
        owner=user,
        name="Recovery Agent",
        objective="Test recovery",
        status=Agent.Status.ACTIVE,
        graph={"nodes": [{"id": "work", "title": "Work", "type": "llm"}], "edges": []},
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Recover me",
        state=AgentRun.State.RUNNING,
        started_at=timezone.now() - timedelta(hours=1),
    )
    step = AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=1,
        node_id="work",
        title="Work",
        action_type="llm",
        state=AgentStepRun.State.RUNNING,
        started_at=timezone.now() - timedelta(hours=1),
    )
    reservations = [
        reserve(user, Decimal("3.00"), f"agent-run:{run.id}"),
        reserve(user, Decimal("4.00"), f"agent-run:{run.id}:step:1"),
        reserve(user, Decimal("5.00"), f"agent-team:{run.id}:step:2"),
    ]
    AgentRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=1))

    assert recover_stale_agent_runs() == 1

    run.refresh_from_db()
    step.refresh_from_db()
    user.wallet.refresh_from_db()
    assert run.state == AgentRun.State.FAILED
    assert run.error_code == "stale_agent_run_recovered"
    assert step.state == AgentStepRun.State.FAILED
    for reservation in reservations:
        reservation.refresh_from_db()
        assert reservation.state == BalanceReservation.State.RELEASED
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert user.wallet.available_rub == Decimal("20.0000")


@pytest.mark.django_db(transaction=True)
def test_stale_dev_run_preserves_partial_working_branch_context(monkeypatch):
    monkeypatch.setenv("AGENT_STALE_TIMEOUT_SECONDS", "1200")
    user = get_user_model().objects.create_user(username="dev-stale-branch", password="test-password")
    agent = Agent.objects.create(
        owner=user,
        name="Developer",
        objective="Write code",
        status=Agent.Status.ACTIVE,
    )
    branch = "ai-workspace/run-deadbeef1234"
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Recover partial GitHub write",
        state=AgentRun.State.RUNNING,
        input_payload={"phase": "writing_changes", "working_branch": branch},
        started_at=timezone.now() - timedelta(hours=1),
    )
    step = AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=1,
        node_id="approved-github-write",
        title="Sandbox + GitHub write",
        action_type="sandbox+github_write",
        state=AgentStepRun.State.RUNNING,
        started_at=timezone.now() - timedelta(hours=1),
    )
    AgentRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=1))

    assert recover_stale_agent_runs() == 1

    run.refresh_from_db()
    step.refresh_from_db()
    assert run.state == AgentRun.State.FAILED
    assert branch in run.error_message
    assert "новую изолированную ветку" in run.error_message
    assert branch in step.public_log
    assert run.input_payload["working_branch"] == branch


@pytest.mark.django_db(transaction=True)
def test_waiting_approval_is_never_recovered_as_stale(monkeypatch):
    monkeypatch.setenv("AGENT_STALE_TIMEOUT_SECONDS", "1200")
    user = get_user_model().objects.create_user(
        username="agent-approval",
        email="agent-approval@example.test",
        password="test-password",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Controlled Agent",
        objective="Wait for human",
        status=Agent.Status.ACTIVE,
        autonomy=Agent.Autonomy.CONTROLLED,
        graph={"nodes": [{"id": "approval", "title": "Approval", "type": "approval"}], "edges": []},
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Wait",
        state=AgentRun.State.WAITING_APPROVAL,
    )
    AgentRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(days=2))

    assert recover_stale_agent_runs() == 0
    run.refresh_from_db()
    assert run.state == AgentRun.State.WAITING_APPROVAL
    assert run.error_code == ""


@pytest.mark.django_db(transaction=True)
def test_pending_approval_expires_and_unblocks_run(monkeypatch):
    monkeypatch.setenv("AGENT_APPROVAL_TIMEOUT_HOURS", "24")
    user = get_user_model().objects.create_user(
        username="agent-expired-approval",
        email="agent-expired-approval@example.test",
        password="test-password",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Approval Agent",
        objective="Wait safely",
        status=Agent.Status.ACTIVE,
        autonomy=Agent.Autonomy.CONTROLLED,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Wait",
        state=AgentRun.State.WAITING_APPROVAL,
    )
    approval = AgentApproval.objects.create(
        run=run,
        requested_by_agent=agent,
        title="Confirm",
        action_payload={"kind": "controlled_run_start"},
    )
    AgentApproval.objects.filter(pk=approval.pk).update(created_at=timezone.now() - timedelta(hours=25))

    assert expire_stale_agent_approvals() == 1

    approval.refresh_from_db()
    run.refresh_from_db()
    assert approval.status == AgentApproval.Status.EXPIRED
    assert approval.decided_at is not None
    assert run.state == AgentRun.State.CANCELED
    assert run.error_code == "agent_approval_expired"
    assert run.finished_at is not None


@pytest.mark.django_db(transaction=True)
def test_decided_approval_is_never_expired(monkeypatch):
    monkeypatch.setenv("AGENT_APPROVAL_TIMEOUT_HOURS", "24")
    user = get_user_model().objects.create_user(
        username="agent-decided-approval",
        email="agent-decided-approval@example.test",
        password="test-password",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Approved Agent",
        objective="Approved work",
        status=Agent.Status.ACTIVE,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Approved",
        state=AgentRun.State.RUNNING,
    )
    approval = AgentApproval.objects.create(
        run=run,
        requested_by_agent=agent,
        title="Already approved",
        status=AgentApproval.Status.APPROVED,
        decided_by=user,
        decided_at=timezone.now() - timedelta(hours=25),
    )
    AgentApproval.objects.filter(pk=approval.pk).update(created_at=timezone.now() - timedelta(days=7))

    assert expire_stale_agent_approvals() == 0
    approval.refresh_from_db()
    run.refresh_from_db()
    assert approval.status == AgentApproval.Status.APPROVED
    assert run.state == AgentRun.State.RUNNING
