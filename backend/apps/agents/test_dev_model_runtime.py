from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.accounts.models import User

from .models import Agent, AgentRun, AgentTeam, AgentTeamMember
from .team_runtime import _run_llm_stage


@pytest.mark.django_db
def test_dev_stage_records_selected_fallback_model_and_attempt_evidence():
    user = User.objects.create_user(username="dev-model-runtime", password="StrongPass123!")
    agent = Agent.objects.create(
        owner=user,
        name="Developer",
        role="Software Engineer",
        objective="Implement safely",
        status=Agent.Status.ACTIVE,
        max_cost_rub_per_run=Decimal("50"),
        max_cost_rub_per_day=Decimal("100"),
        max_cost_rub_per_month=Decimal("1000"),
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

    primary = SimpleNamespace(
        slug="primary-model",
        upstream_model="primary-upstream",
        max_output_tokens=4096,
        provider=SimpleNamespace(slug="provider-a"),
    )
    fallback = SimpleNamespace(
        slug="fallback-model",
        upstream_model="fallback-upstream",
        max_output_tokens=4096,
        provider=SimpleNamespace(slug="provider-b"),
    )
    preflight = SimpleNamespace(user_charge_rub=Decimal("5.00"))
    provider_result = SimpleNamespace(
        text="fallback result",
        provider_request_id="req-fallback",
        input_tokens=120,
        output_tokens=80,
    )
    generation = SimpleNamespace(
        result=provider_result,
        model=fallback,
        actual_rub=Decimal("4.25"),
        provider_attempts=1,
        model_attempts=[
            {"rank": 1, "model": "primary-model", "provider": "provider-a", "status": "provider_failed"},
            {"rank": 2, "model": "fallback-model", "provider": "provider-b", "status": "completed"},
        ],
    )
    budget_snapshot = {
        "run_spend": Decimal("0"),
        "run_limit": Decimal("50"),
        "day_spend": Decimal("0"),
        "day_limit": Decimal("100"),
        "month_spend": Decimal("0"),
        "month_limit": Decimal("1000"),
    }

    with (
        patch("apps.agents.team_runtime._model_for", return_value=primary),
        patch("apps.agents.team_runtime.active_price", return_value=SimpleNamespace()),
        patch("apps.agents.team_runtime.quote", return_value=preflight),
        patch("apps.agents.team_runtime.require_margin", side_effect=lambda value: value),
        patch(
            "apps.agents.team_runtime.effective_remaining_budget",
            return_value=(Decimal("50"), budget_snapshot),
        ),
        patch("apps.agents.team_runtime.execute_with_model_fallback", return_value=generation) as execute_fallback,
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

    assert terminal is None
    assert text == "fallback result"
    assert total == Decimal("4.25")
    step = run.steps.get(sequence=1)
    assert step.state == step.State.COMPLETED
    assert step.cost_rub == Decimal("4.25")
    assert step.output_payload["primary_model"] == "primary-model"
    assert step.output_payload["model"] == "fallback-model"
    assert [item["status"] for item in step.output_payload["model_attempts"]] == ["provider_failed", "completed"]
    execute_fallback.assert_called_once()
    assert execute_fallback.call_args.kwargs["remaining_budget_rub"] == Decimal("50")
