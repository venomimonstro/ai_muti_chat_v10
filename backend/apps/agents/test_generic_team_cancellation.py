from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.accounts.models import User

from .generic_team_runtime import execute_generic_team_run
from .models import Agent, AgentRun, AgentTeam, AgentTeamMember


@pytest.mark.django_db
def test_generic_team_cancel_after_provider_accounts_cost_and_stops_next_member():
    user = User.objects.create_user(
        username="generic-team-cancel",
        email="generic-team-cancel@example.com",
        password="StrongPass123!",
    )
    first = Agent.objects.create(
        owner=user,
        name="First",
        role="Researcher",
        objective="Research",
        status=Agent.Status.ACTIVE,
        system_level="balanced",
    )
    second = Agent.objects.create(
        owner=user,
        name="Second",
        role="Writer",
        objective="Write",
        status=Agent.Status.ACTIVE,
        system_level="balanced",
    )
    team = AgentTeam.objects.create(
        owner=user,
        name="Content Team",
        objective="Create content",
        director=first,
        max_cost_rub_per_run=Decimal("100"),
    )
    AgentTeamMember.objects.create(team=team, agent=first, role="Research", priority=10, can_delegate=True)
    AgentTeamMember.objects.create(team=team, agent=second, role="Writing", priority=20)
    run = AgentRun.objects.create(
        owner=user,
        team=team,
        objective="Prepare article",
        state=AgentRun.State.QUEUED,
    )

    model = SimpleNamespace(
        slug="fake-model",
        upstream_model="fake-upstream",
        max_output_tokens=2048,
        provider=SimpleNamespace(slug="fake-provider"),
    )
    quoted = SimpleNamespace(
        user_charge_rub=Decimal("2.0000"),
        provider_cost_rub=Decimal("1.0000"),
        fx_snapshot=None,
    )
    customer_reservation = SimpleNamespace(id="customer-reservation", amount_rub=Decimal("2.0000"))
    provider_reservation = SimpleNamespace(id="provider-reservation")

    class Adapter:
        def generate(self, **kwargs):
            AgentRun.objects.filter(pk=run.id).update(state=AgentRun.State.CANCELED)
            return SimpleNamespace(
                text="THIS MUST NOT BE PUBLISHED",
                input_tokens=10,
                output_tokens=5,
                provider_request_id="provider-request",
            )

    with patch("apps.agents.generic_team_runtime._model_for", return_value=model), patch(
        "apps.agents.generic_team_runtime.estimate_message_tokens", return_value=20
    ), patch("apps.agents.generic_team_runtime.active_price", return_value=object()), patch(
        "apps.agents.generic_team_runtime.quote", return_value=quoted
    ), patch("apps.agents.generic_team_runtime.require_margin", side_effect=lambda value: value), patch(
        "apps.agents.generic_team_runtime.effective_remaining_budget",
        return_value=(Decimal("100"), {"day_spend": Decimal("0"), "day_limit": Decimal("100"), "month_spend": Decimal("0"), "month_limit": Decimal("1000")}),
    ), patch("apps.agents.generic_team_runtime.reserve", return_value=customer_reservation), patch(
        "apps.agents.generic_team_runtime.reserve_agent_provider_spend", return_value=provider_reservation
    ), patch("apps.agents.generic_team_runtime.settle_agent_provider_spend"), patch(
        "apps.agents.generic_team_runtime.settle"
    ), patch("apps.agents.generic_team_runtime.adapter_for", return_value=Adapter()):
        result = execute_generic_team_run(run.id)

    result.refresh_from_db()
    assert result.state == AgentRun.State.CANCELED
    assert result.cost_actual_rub == Decimal("2.0000")
    assert result.steps.count() == 1
    step = result.steps.get()
    assert step.agent_id == first.id
    assert step.cost_rub == Decimal("2.0000")
    assert step.output_payload.get("canceled_after_provider") is True
    assert "THIS MUST NOT BE PUBLISHED" not in step.public_log
    assert result.steps.filter(agent=second).exists() is False
