import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import User

from .models import Agent, AgentApproval, AgentRun


@pytest.mark.django_db
def test_agent_system_audit_accepts_consistent_waiting_approval():
    user = User.objects.create_user(
        username="audit-waiting",
        email="audit-waiting@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Controlled employee",
        objective="Prepare result and wait",
        status=Agent.Status.DRAFT,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Prepare result",
        state=AgentRun.State.WAITING_APPROVAL,
    )
    AgentApproval.objects.create(
        run=run,
        requested_by_agent=agent,
        title="Continue?",
        status=AgentApproval.Status.PENDING,
    )

    call_command("agent_system_audit")


@pytest.mark.django_db
def test_agent_system_audit_rejects_pending_approval_on_non_waiting_run():
    user = User.objects.create_user(
        username="audit-bad-approval",
        email="audit-bad-approval@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Bad approval employee",
        objective="Work",
        status=Agent.Status.DRAFT,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Work",
        state=AgentRun.State.RUNNING,
    )
    AgentApproval.objects.create(
        run=run,
        requested_by_agent=agent,
        title="Stale approval",
        status=AgentApproval.Status.PENDING,
    )

    with pytest.raises(CommandError, match="Agent system audit failed"):
        call_command("agent_system_audit")


@pytest.mark.django_db
def test_agent_system_audit_rejects_terminal_run_without_finished_at():
    user = User.objects.create_user(
        username="audit-terminal",
        email="audit-terminal@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Terminal employee",
        objective="Work",
        status=Agent.Status.DRAFT,
    )
    AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Work",
        state=AgentRun.State.COMPLETED,
        finished_at=None,
    )

    with pytest.raises(CommandError, match="Agent system audit failed"):
        call_command("agent_system_audit")


@pytest.mark.django_db
def test_agent_system_audit_accepts_finished_terminal_run():
    user = User.objects.create_user(
        username="audit-terminal-ok",
        email="audit-terminal-ok@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Finished employee",
        objective="Work",
        status=Agent.Status.DRAFT,
    )
    AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Work",
        state=AgentRun.State.COMPLETED,
        finished_at=timezone.now(),
    )

    call_command("agent_system_audit")
