from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.ai_registry.models import AIModel, Provider, ProviderApiKey
from apps.billing.models import BalanceReservation
from apps.billing.services import credit, reserve
from apps.procurement.models import ProviderSpend, ProviderSpendReservation
from apps.procurement.services import create_funding_account, record_purchase

from .accounting import checkpoint_agent_provider_delivery, reserve_agent_provider_spend
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


@pytest.mark.django_db(transaction=True)
def test_stale_agent_reconciles_confirmed_provider_delivery_before_releasing_customer(monkeypatch):
    monkeypatch.setenv("AGENT_STALE_TIMEOUT_SECONDS", "1200")
    user = get_user_model().objects.create_user(
        username="agent-settlement-recovery",
        email="agent-settlement-recovery@example.test",
        password="test-password",
    )
    credit(user, Decimal("20"), "test", "agent-settlement-recovery")
    agent = Agent.objects.create(
        owner=user,
        name="Settlement Recovery Agent",
        objective="Recover confirmed provider usage",
        status=Agent.Status.ACTIVE,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Recover provider settlement",
        state=AgentRun.State.REVIEWING,
        started_at=timezone.now() - timedelta(hours=1),
    )
    step = AgentStepRun.objects.create(
        run=run,
        agent=agent,
        sequence=1,
        node_id="llm",
        title="LLM",
        action_type="llm",
        state=AgentStepRun.State.RUNNING,
        started_at=timezone.now() - timedelta(hours=1),
    )

    provider = Provider.objects.create(
        slug="agent-recovery-provider",
        name="Agent recovery provider",
        adapter_type=Provider.AdapterType.OPENAI_RESPONSES,
        enabled=True,
        health_state=Provider.HealthState.HEALTHY,
    )
    key = ProviderApiKey(
        provider=provider,
        label="healthy",
        enabled=True,
        health_state=ProviderApiKey.HealthState.HEALTHY,
    )
    key.set_secret("sk-agent-recovery")
    key.save()
    model = AIModel.objects.create(
        provider=provider,
        slug="agent-recovery-model",
        display_name="Agent recovery model",
        upstream_model="upstream-recovery",
        enabled=True,
    )
    account = create_funding_account(
        provider=provider,
        api_key=key,
        label="Recovery funding",
        currency="USD",
        is_default=True,
    )
    record_purchase(
        account=account,
        credit_native=Decimal("10"),
        base_cost_rub=Decimal("1000"),
        created_by=user,
    )

    customer = reserve(user, Decimal("3"), f"agent-run:{run.id}")
    provider_reservation = reserve_agent_provider_spend(
        model=model,
        provider_cost_rub=Decimal("2"),
        fx_snapshot=SimpleNamespace(rate=Decimal("100")),
        source_key=f"agent:{run.id}",
        provider_currency="USD",
    )
    assert provider_reservation is not None

    checkpoint_agent_provider_delivery(
        step=step,
        model=model,
        result=SimpleNamespace(
            provider_request_id="provider-recovery-1",
            input_tokens=100,
            output_tokens=50,
        ),
        actual_quote=SimpleNamespace(
            provider_cost_rub=Decimal("1.5"),
            fx_snapshot=SimpleNamespace(rate=Decimal("100")),
        ),
        provider_reservation=provider_reservation,
        customer_reservation=customer,
        source_id=run.id,
        customer_charge=Decimal("3"),
    )
    AgentRun.objects.filter(pk=run.pk).update(
        updated_at=timezone.now() - timedelta(hours=1)
    )

    assert recover_stale_agent_runs() == 1

    run.refresh_from_db()
    step.refresh_from_db()
    customer.refresh_from_db()
    provider_reservation.refresh_from_db()
    account.refresh_from_db()
    user.wallet.refresh_from_db()
    spend = ProviderSpend.objects.get(source_type="agent", source_id=str(run.id))

    assert run.state == AgentRun.State.FAILED
    assert run.error_code == "stale_agent_run_recovered"
    assert step.state == AgentStepRun.State.FAILED
    assert (step.output_payload["_provider_settlement"]["status"]) == "settled"
    assert customer.state == BalanceReservation.State.RELEASED
    assert customer.actual_rub is None
    assert provider_reservation.state == ProviderSpendReservation.State.SETTLED
    assert spend.native_cost == Decimal("0.015000")
    assert spend.customer_charge_rub == Decimal("0.0000")
    assert account.reserved_native == Decimal("0.000000")
    assert account.spent_native == Decimal("0.015000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert user.wallet.available_rub == Decimal("20.0000")
