import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.github_integration.models import GitHubInstallation, GitHubRepositoryBinding
from apps.projects.models import Project

from .models import Agent, AgentTeam


@pytest.mark.django_db
def test_dev_bootstrap_retry_reuses_fresh_team_without_duplicate_agents():
    user = User.objects.create_user(username="dev-bootstrap", email="dev-bootstrap@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=user, name="Dev project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=123456,
        account_login="dev-user",
        account_type="User",
        repository_selection="selected",
        permissions={"contents": "write"},
        active=True,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=777,
        full_name="dev-user/project",
        default_branch="main",
        private=True,
        write_enabled=True,
    )
    client = APIClient()
    client.force_authenticate(user)
    payload = {"project": str(project.id), "objective": "Исправить критические ошибки и добавить тесты"}

    first = client.post("/api/v1/agent-teams/bootstrap-dev/", payload, format="json")
    second = client.post("/api/v1/agent-teams/bootstrap-dev/", payload, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.data["id"] == first.data["id"]
    assert second.data["reused"] is True
    assert AgentTeam.objects.filter(owner=user, project=project, kind=AgentTeam.Kind.DEVELOPMENT).count() == 1
    assert Agent.objects.filter(owner=user, project=project).count() == 4
