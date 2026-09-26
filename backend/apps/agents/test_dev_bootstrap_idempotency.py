import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.github_integration.models import GitHubInstallation, GitHubRepositoryBinding
from apps.projects.models import Project

from .models import Agent, AgentTeam


def _project_with_github(user, *, permission="write", write_enabled=True, active=True, suffix=0):
    project = Project.objects.create(owner=user, name=f"Dev project {suffix}")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=123456 + suffix,
        account_login="dev-user",
        account_type="User",
        repository_selection="selected",
        permissions={"contents": permission},
        active=active,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=777 + suffix,
        full_name=f"dev-user/project-{suffix}",
        default_branch="main",
        private=True,
        write_enabled=write_enabled,
    )
    return project


@pytest.mark.django_db
def test_dev_bootstrap_retry_reuses_fresh_team_without_duplicate_agents():
    user = User.objects.create_user(username="dev-bootstrap", email="dev-bootstrap@example.com", password="StrongPass123!")
    project = _project_with_github(user)
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


@pytest.mark.django_db
def test_dev_bootstrap_rejects_read_only_github_permission_before_creating_agents():
    user = User.objects.create_user(username="dev-bootstrap-read", password="StrongPass123!")
    project = _project_with_github(user, permission="read", suffix=10)
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/agent-teams/bootstrap-dev/",
        {"project": str(project.id), "objective": "Implement feature"},
        format="json",
    )

    assert response.status_code == 400
    assert "contents:write" in str(response.data)
    assert AgentTeam.objects.filter(owner=user, project=project).count() == 0
    assert Agent.objects.filter(owner=user, project=project).count() == 0


@pytest.mark.django_db
def test_dev_bootstrap_rejects_binding_with_app_write_disabled():
    user = User.objects.create_user(username="dev-bootstrap-disabled", password="StrongPass123!")
    project = _project_with_github(user, write_enabled=False, suffix=20)
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/agent-teams/bootstrap-dev/",
        {"project": str(project.id), "objective": "Implement feature"},
        format="json",
    )

    assert response.status_code == 400
    assert "рабочую ветку" in str(response.data)
    assert AgentTeam.objects.filter(owner=user, project=project).count() == 0


@pytest.mark.django_db
def test_dev_bootstrap_rejects_inactive_github_installation():
    user = User.objects.create_user(username="dev-bootstrap-inactive", password="StrongPass123!")
    project = _project_with_github(user, active=False, suffix=30)
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/agent-teams/bootstrap-dev/",
        {"project": str(project.id), "objective": "Implement feature"},
        format="json",
    )

    assert response.status_code == 400
    assert "installation" in str(response.data)
    assert AgentTeam.objects.filter(owner=user, project=project).count() == 0
