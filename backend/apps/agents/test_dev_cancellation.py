from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.accounts.models import User
from apps.projects.models import Project

from .models import Agent, AgentApproval, AgentRun, AgentTeam
from .team_runtime import _continue_approved_write, _run_llm_stage


@pytest.fixture
def dev_subjects(db):
    user = User.objects.create_user(username="dev-cancel", email="dev-cancel@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=user, name="Dev project")
    director = Agent.objects.create(
        owner=user,
        project=project,
        name="Engineering Director",
        role="Engineering Director",
        objective="Direct",
        status=Agent.Status.ACTIVE,
    )
    team = AgentTeam.objects.create(
        owner=user,
        project=project,
        name="Dev Team",
        objective="Build safely",
        kind=AgentTeam.Kind.DEVELOPMENT,
        director=director,
        max_cost_rub_per_run=Decimal("100"),
    )
    return user, project, director, team


@pytest.mark.django_db
def test_canceled_dev_run_does_not_start_llm_stage(dev_subjects):
    user, project, director, team = dev_subjects
    run = AgentRun.objects.create(
        owner=user,
        team=team,
        project=project,
        objective="Do work",
        state=AgentRun.State.CANCELED,
    )

    with patch("apps.agents.team_runtime._model_for") as model_for, patch("apps.agents.team_runtime.adapter_for") as adapter_for_mock:
        text, total, terminal = _run_llm_stage(
            run=run,
            agent=director,
            role="Architecture",
            repository_context=None,
            previous=[],
            sequence=1,
            total=Decimal("0"),
            budget=Decimal("100"),
        )

    assert text is None
    assert total == Decimal("0")
    assert terminal.id == run.id
    model_for.assert_not_called()
    adapter_for_mock.assert_not_called()
    assert run.steps.count() == 0


@pytest.mark.django_db
def test_canceled_dev_run_never_writes_to_github_after_approval(dev_subjects):
    user, project, director, team = dev_subjects
    run = AgentRun.objects.create(
        owner=user,
        team=team,
        project=project,
        objective="Do work",
        state=AgentRun.State.CANCELED,
    )
    approval = AgentApproval.objects.create(
        run=run,
        requested_by_agent=director,
        title="GitHub write",
        status=AgentApproval.Status.APPROVED,
        action_payload={"kind": "github_changes", "changes": [{"path": "README.md", "operation": "update"}]},
    )

    with patch("apps.agents.team_runtime.apply_approved_changes") as apply_changes:
        result = _continue_approved_write(
            run=run,
            approval=approval,
            members=[],
            total=Decimal("0"),
            budget=Decimal("100"),
        )

    assert result.id == run.id
    result.refresh_from_db()
    assert result.state == AgentRun.State.CANCELED
    apply_changes.assert_not_called()
