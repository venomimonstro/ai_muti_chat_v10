from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project

from .models import Agent, AgentApproval, AgentRun, AgentTeam


@pytest.mark.django_db
def test_dev_diff_preview_is_owner_scoped_and_side_effect_free():
    owner = User.objects.create_user(username="dev-safe", email="dev-safe@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="dev-other", email="dev-other@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=owner, name="Repo")
    director = Agent.objects.create(owner=owner, project=project, name="Director", objective="Lead", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=owner, project=project, name="Dev", objective="Change", kind=AgentTeam.Kind.DEVELOPMENT, director=director)
    run = AgentRun.objects.create(owner=owner, team=team, project=project, objective="Change", state=AgentRun.State.WAITING_APPROVAL)
    AgentApproval.objects.create(
        run=run,
        requested_by_agent=director,
        title="Approve",
        action_payload={"kind": "github_changes", "base_branch": "main", "changes": []},
    )

    client = APIClient()
    client.force_authenticate(other)
    assert client.get(f"/api/v1/agent-runs/{run.id}/changes-preview/").status_code == 404


@pytest.mark.django_db
def test_abandon_requires_exact_run_branch_and_never_claims_remote_delete():
    owner = User.objects.create_user(username="dev-abandon", email="dev-abandon@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=owner, name="Repo")
    director = Agent.objects.create(owner=owner, project=project, name="Director", objective="Lead", status=Agent.Status.ACTIVE)
    team = AgentTeam.objects.create(owner=owner, project=project, name="Dev", objective="Change", kind=AgentTeam.Kind.DEVELOPMENT, director=director)
    run = AgentRun.objects.create(owner=owner, team=team, project=project, objective="Change", state=AgentRun.State.COMPLETED)
    branch = f"ai-workspace/run-{str(run.id).replace('-', '')[:12]}"
    run.output_payload = {"working_branch": branch}
    run.save(update_fields=["output_payload", "updated_at"])

    client = APIClient()
    client.force_authenticate(owner)
    denied = client.post(f"/api/v1/agent-runs/{run.id}/abandon-branch/", {}, format="json")
    assert denied.status_code == 400

    accepted = client.post(
        f"/api/v1/agent-runs/{run.id}/abandon-branch/",
        {"confirm_abandon": True},
        format="json",
    )
    assert accepted.status_code == 200
    assert accepted.data["default_branch_unchanged"] is True
    assert accepted.data["remote_branch_deleted"] is False
    run.refresh_from_db()
    assert run.output_payload["branch_disposition"] == "abandoned"
