from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import BalanceReservation, Wallet
from apps.billing.services import credit, reserve

from .models import Agent, AgentRun
from .recovery import recover_agent_run


@pytest.mark.django_db
def test_stale_graph_run_releases_customer_reservation():
    user = User.objects.create_user(
        username="graph-recovery",
        email="graph-recovery@example.com",
        password="StrongPass123!",
    )
    credit(user, Decimal("100.00"), "test", "graph-recovery-funding")
    agent = Agent.objects.create(
        owner=user,
        name="Graph agent",
        objective="Run graph",
        status=Agent.Status.ACTIVE,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Interrupted graph",
        state=AgentRun.State.RUNNING,
    )
    reservation = reserve(user, Decimal("10.00"), f"agent-graph:{run.id}:research")
    AgentRun.objects.filter(pk=run.id).update(updated_at=timezone.now() - timedelta(hours=2))

    assert recover_agent_run(run.id) is True

    reservation.refresh_from_db()
    run.refresh_from_db()
    wallet = Wallet.objects.get(user=user)
    assert reservation.state == BalanceReservation.State.RELEASED
    assert run.state == AgentRun.State.FAILED
    assert run.error_code == "stale_agent_run_recovered"
    assert wallet.reserved_rub == Decimal("0.0000")
    assert wallet.available_rub == Decimal("100.0000")
