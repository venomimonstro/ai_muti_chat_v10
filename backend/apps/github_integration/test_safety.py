import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project

from .models import GitHubInstallation, GitHubRepositoryBinding
from .safety import is_sensitive_path


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        "backend/.env.local",
        ".github/workflows/deploy.yml",
        ".github/actions/build/action.yml",
        "certs/server.pem",
        "keys/private.key",
        "credentials.json",
    ],
)
def test_sensitive_repository_paths_are_detected(path):
    assert is_sensitive_path(path) is True


@pytest.mark.django_db
def test_github_routes_fail_closed_when_integration_disabled(monkeypatch):
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "false")
    user = User.objects.create_user(
        username="github-off", email="github-off@example.test", password="password123"
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.get("/api/v1/github/connect/")

    assert response.status_code == 503
    assert "отключена" in str(response.data)


@pytest.mark.django_db
def test_sensitive_file_read_is_blocked_before_remote_github_call(monkeypatch):
    user = User.objects.create_user(
        username="github-sensitive", email="github-sensitive@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Sensitive project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=9101,
        account_login="sensitive-user",
        permissions={"contents": "write"},
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=9202,
        full_name="sensitive-user/private",
        write_enabled=True,
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.get(
        f"/api/v1/projects/{project.id}/github/file/",
        {"path": ".github/workflows/deploy.yml"},
    )

    assert response.status_code == 403
    assert "политикой безопасности" in str(response.data)


@pytest.mark.django_db
def test_sensitive_write_can_only_be_unlocked_by_explicit_server_policy(monkeypatch):
    user = User.objects.create_user(
        username="github-sensitive-write",
        email="github-sensitive-write@example.test",
        password="password123",
    )
    project = Project.objects.create(owner=user, name="Sensitive write project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=9303,
        account_login="writer",
        permissions={"contents": "write"},
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=9404,
        full_name="writer/private",
        write_enabled=True,
    )
    client = APIClient()
    client.force_authenticate(user)

    blocked = client.put(
        f"/api/v1/projects/{project.id}/github/file/",
        {
            "path": ".env.production",
            "content": "SECRET=changed",
            "expected_sha": "a" * 40,
            "confirm_write": True,
        },
        format="json",
    )
    assert blocked.status_code == 403

    monkeypatch.setenv("GITHUB_ALLOW_SENSITIVE_PATHS", "true")
    # Once explicitly unlocked at server level the request reaches the normal GitHub service layer.
    # No real remote call is performed in this test, so a 403 from the route guard must no longer occur.
    allowed = client.put(
        f"/api/v1/projects/{project.id}/github/file/",
        {
            "path": ".env.production",
            "content": "SECRET=changed",
            "expected_sha": "a" * 40,
            "confirm_write": True,
        },
        format="json",
    )
    assert allowed.status_code != 403
