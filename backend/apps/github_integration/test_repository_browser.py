from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project

from .models import GitHubInstallation, GitHubRepositoryBinding
from .services import list_repository_directory


def test_directory_browser_uses_repo_scoped_read_token_and_sorts_directories_first(monkeypatch):
    binding = SimpleNamespace(
        installation=SimpleNamespace(installation_id=101),
        repository_id=202,
        full_name="owner/repo",
        default_branch="main",
    )
    token_calls = []
    request_calls = []

    def fake_token(installation_id, **kwargs):
        token_calls.append((installation_id, kwargs))
        return "installation-token"

    def fake_request(method, url, **kwargs):
        request_calls.append((method, url, kwargs))
        return [
            {"name": "z.py", "path": "src/z.py", "type": "file", "size": 12, "sha": "a" * 40},
            {"name": "src", "path": "src", "type": "dir", "size": 0, "sha": "b" * 40},
            {"name": "submodule", "path": "vendor", "type": "submodule", "size": 0, "sha": "c" * 40},
        ]

    monkeypatch.setattr("apps.github_integration.services.installation_token", fake_token)
    monkeypatch.setattr("apps.github_integration.services._json_request", fake_request)

    result = list_repository_directory(binding)

    assert [item["name"] for item in result["items"]] == ["src", "z.py"]
    assert token_calls == [(101, {"repository_ids": [202], "permissions": {"contents": "read"}})]
    assert request_calls[0][0] == "GET"
    assert request_calls[0][1].endswith("/repos/owner/repo/contents")
    assert request_calls[0][2]["params"] == {"ref": "main"}


def test_directory_browser_rejects_path_traversal_before_remote_call(monkeypatch):
    binding = SimpleNamespace(
        installation=SimpleNamespace(installation_id=101),
        repository_id=202,
        full_name="owner/repo",
        default_branch="main",
    )
    called = False

    def should_not_call(*_args, **_kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr("apps.github_integration.services._json_request", should_not_call)
    with pytest.raises(ValidationError):
        list_repository_directory(binding, "src/../secrets")
    assert called is False


@pytest.mark.django_db
def test_repository_browser_never_exposes_another_users_project(monkeypatch):
    owner = User.objects.create_user(
        username="repo-owner", email="repo-owner@example.test", password="password123"
    )
    outsider = User.objects.create_user(
        username="repo-outsider", email="repo-outsider@example.test", password="password123"
    )
    project = Project.objects.create(owner=owner, name="Private")
    installation = GitHubInstallation.objects.create(
        owner=owner, installation_id=303, account_login="owner"
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=404,
        full_name="owner/private",
    )
    client = APIClient()
    client.force_authenticate(outsider)
    monkeypatch.setattr(
        "apps.github_integration.views.list_repository_directory",
        lambda *_args, **_kwargs: {"path": "", "ref": "main", "items": []},
    )

    response = client.get(f"/api/v1/projects/{project.id}/github/tree/")

    assert response.status_code == 404
