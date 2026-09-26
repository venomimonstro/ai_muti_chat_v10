import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent, AgentApproval, AgentRun


@pytest.mark.django_db
def test_cancel_waiting_run_rejects_pending_approvals():
    user = User.objects.create_user(
        username="cancel-waiting",
        email="cancel-waiting@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Controlled employee",
        objective="Prepare and wait",
        status=Agent.Status.DRAFT,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Prepare",
        state=AgentRun.State.WAITING_APPROVAL,
    )
    approval = AgentApproval.objects.create(
        run=run,
        requested_by_agent=agent,
        title="Publish?",
        status=AgentApproval.Status.PENDING,
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(f"/api/v1/agent-runs/{run.id}/cancel/", {}, format="json")

    assert response.status_code == 200
    run.refresh_from_db()
    approval.refresh_from_db()
    assert run.state == AgentRun.State.CANCELED
    assert run.error_code == "user_canceled"
    assert run.finished_at is not None
    assert approval.status == AgentApproval.Status.REJECTED
    assert approval.decided_by_id == user.id
    assert approval.decided_at is not None


@pytest.mark.django_db
def test_cancel_does_not_rewrite_budget_exceeded_terminal_state():
    user = User.objects.create_user(
        username="cancel-budget",
        email="cancel-budget@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Budget employee",
        objective="Work",
        status=Agent.Status.DRAFT,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Work",
        state=AgentRun.State.BUDGET_EXCEEDED,
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(f"/api/v1/agent-runs/{run.id}/cancel/", {}, format="json")

    assert response.status_code == 200
    run.refresh_from_db()
    assert run.state == AgentRun.State.BUDGET_EXCEEDED
