from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project

from .models import Agent, AgentApproval, AgentRun, AgentTeam, AgentTeamMember


@pytest.mark.django_db
def test_dev_write_approval_stays_pending_when_repository_became_unavailable():
    user = User.objects.create_user(username="dev-approval-recheck", password="StrongPass123!")
    project = Project.objects.create(owner=user, name="Dev project")
    director = Agent.objects.create(
        owner=user,
        project=project,
        name="Engineering Director",
        role="Engineering Director",
        objective="Lead development",
        status=Agent.Status.ACTIVE,
    )
    team = AgentTeam.objects.create(
        owner=user,
        project=project,
        name="Dev Team",
        objective="Fix project",
        kind=AgentTeam.Kind.DEVELOPMENT,
        director=director,
        active=True,
    )
    AgentTeamMember.objects.create(
        team=team,
        agent=director,
        role="Engineering Director",
        priority=10,
        can_delegate=True,
        enabled=True,
    )
    run = AgentRun.objects.create(
        owner=user,
        team=team,
        project=project,
        objective="Fix project",
        state=AgentRun.State.WAITING_APPROVAL,
        input_payload={"phase": "awaiting_github_approval"},
    )
    approval = AgentApproval.objects.create(
        run=run,
        requested_by_agent=director,
        title="Allow GitHub changes",
        action_payload={"kind": "github_changes", "changes": []},
        status=AgentApproval.Status.PENDING,
    )
    client = APIClient()
    client.force_authenticate(user)

    with (
        patch(
            "apps.agents.approval_views.team_readiness",
            return_value={"ready": True, "blockers": [], "warnings": [], "checks": {}, "models": []},
        ),
        patch(
            "apps.agents.approval_views.build_repository_context",
            side_effect=RuntimeError("installation revoked"),
        ),
        patch("apps.agents.tasks.execute_agent_run_task.delay") as delay,
    ):
        response = client.post(
            f"/api/v1/agent-runs/{run.id}/approvals/{approval.id}/decision/",
            {"decision": "approved"},
            format="json",
        )

    assert response.status_code == 400
    approval.refresh_from_db()
    run.refresh_from_db()
    assert approval.status == AgentApproval.Status.PENDING
    assert run.state == AgentRun.State.WAITING_APPROVAL
    delay.assert_not_called()
