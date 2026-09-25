from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve

from .models import Agent, AgentRun, AgentStepRun
from .recovery import recover_stale_agent_runs


@pytest.mark.django_db(transaction=True)
def test_stale_agent_run_releases_customer_reserve_and_fails_run(monkeypatch):
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
    reservation = reserve(
        user,
        Decimal("5.00"),
        f"agent-run:{run.id}:step:1",
    )
    stale_at = timezone.now() - timedelta(hours=1)
    AgentRun.objects.filter(pk=run.pk).update(updated_at=stale_at)

    assert recover_stale_agent_runs() == 1

    run.refresh_from_db()
    step.refresh_from_db()
    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert run.state == AgentRun.State.FAILED
    assert run.error_code == "stale_agent_run_recovered"
    assert step.state == AgentStepRun.State.FAILED
    assert reservation.state == BalanceReservation.State.RELEASED
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert user.wallet.available_rub == Decimal("20.0000")


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
