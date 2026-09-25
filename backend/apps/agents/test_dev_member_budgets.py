from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.accounts.models import User

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember
from .team_runtime import _run_llm_stage


@pytest.mark.django_db
def test_dev_stage_does_not_call_provider_when_member_period_budget_is_exhausted():
    user = User.objects.create_user(username="dev-member-budget", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Developer",
        role="Software Engineer",
        objective="Implement safely",
        status=Agent.Status.ACTIVE,
        max_cost_rub_per_run=Decimal("25"),
        max_cost_rub_per_day=Decimal("25"),
        max_cost_rub_per_month=Decimal("100"),
    )
    team = AgentTeam.objects.create(
        owner=user,
        name="Dev Team",
        objective="Fix code",
        kind=AgentTeam.Kind.DEVELOPMENT,
        director=agent,
        max_cost_rub_per_run=Decimal("100"),
    )
    AgentTeamMember.objects.create(team=team, agent=agent, role="Development", priority=10, enabled=True)
    run = AgentRun.objects.create(owner=user, team=team, objective="Fix code", state=AgentRun.State.RUNNING)
    model = SimpleNamespace(
        slug="system-pro",
        upstream_model="system-pro-upstream",
        max_output_tokens=4096,
        provider=SimpleNamespace(slug="gigachat"),
    )
    quote_result = SimpleNamespace(user_charge_rub=Decimal("5.00"))
    budget_snapshot = {
        "run_spend": Decimal("0"),
        "run_limit": Decimal("25"),
        "day_spend": Decimal("25"),
        "day_limit": Decimal("25"),
        "month_spend": Decimal("25"),
        "month_limit": Decimal("100"),
    }

    with (
        patch("apps.agents.team_runtime._model_for", return_value=model),
        patch("apps.agents.team_runtime.active_price", return_value=SimpleNamespace()),
        patch("apps.agents.team_runtime.quote", return_value=quote_result),
        patch("apps.agents.team_runtime.require_margin", side_effect=lambda value: value),
        patch(
            "apps.agents.team_runtime.effective_remaining_budget",
            return_value=(Decimal("0"), budget_snapshot),
        ),
        patch("apps.agents.team_runtime.reserve") as reserve_customer,
        patch("apps.agents.team_runtime.reserve_agent_provider_spend") as reserve_provider,
        patch("apps.agents.team_runtime.adapter_for") as adapter,
    ):
        text, total, terminal = _run_llm_stage(
            run=run,
            agent=agent,
            role="Development",
            repository_context={"rendered": "tree"},
            previous=[],
            sequence=1,
            total=Decimal("0"),
            budget=Decimal("100"),
        )

    assert text is None
    assert total == Decimal("0")
    assert terminal is not None
    run.refresh_from_db()
    assert run.state == AgentRun.State.BUDGET_EXCEEDED
    assert run.error_code == "agent_period_budget_exceeded"
    reserve_customer.assert_not_called()
    reserve_provider.assert_not_called()
    adapter.assert_not_called()
