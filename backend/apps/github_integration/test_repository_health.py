from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project

from .models import GitHubInstallation, GitHubRepositoryBinding


@pytest.mark.django_db
def test_repository_health_is_owner_scoped_and_checks_live_read_access():
    owner = User.objects.create_user(username="gh-health", email="gh-health@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="gh-health-other", email="gh-health-other@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=owner, name="Repository project")
    installation = GitHubInstallation.objects.create(
        owner=owner,
        installation_id=444001,
        account_login="owner",
        account_type="User",
        repository_selection="selected",
        permissions={"contents": "read"},
        active=True,
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=991,
        full_name="owner/repository",
        default_branch="main",
        private=True,
        write_enabled=False,
    )
    client = APIClient()
    client.force_authenticate(owner)

    with patch("apps.github_integration.dev_views.list_repository_directory") as tree:
        tree.return_value = {"path": "", "ref": "main", "items": [{"name": "README.md"}]}
        response = client.get(f"/api/v1/projects/{project.id}/github/health/")

    assert response.status_code == 200
    assert response.data["healthy"] is True
    assert response.data["read_ok"] is True
    assert response.data["write_ready"] is False
    assert response.data["root_items"] == 1

    client.force_authenticate(other)
    forbidden = client.get(f"/api/v1/projects/{project.id}/github/health/")
    assert forbidden.status_code == 404


@pytest.mark.django_db
def test_repository_health_never_reports_write_ready_without_contents_write_permission():
    owner = User.objects.create_user(username="gh-health-write", email="gh-health-write@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=owner, name="Repository project")
    installation = GitHubInstallation.objects.create(
        owner=owner,
        installation_id=444002,
        account_login="owner",
        account_type="User",
        repository_selection="selected",
        permissions={"contents": "read"},
        active=True,
    )
    binding = GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=992,
        full_name="owner/repository",
        default_branch="main",
        private=True,
        write_enabled=False,
    )
    # Simulate a legacy/corrupted DB value without going through model save validation.
    GitHubRepositoryBinding.objects.filter(pk=binding.pk).update(write_enabled=True)
    client = APIClient()
    client.force_authenticate(owner)

    with patch("apps.github_integration.dev_views.list_repository_directory") as tree:
        tree.return_value = {"path": "", "ref": "main", "items": []}
        response = client.get(f"/api/v1/projects/{project.id}/github/health/")

    assert response.status_code == 200
    assert response.data["read_ok"] is True
    assert response.data["write_enabled"] is True
    assert response.data["contents_permission"] == "read"
    assert response.data["write_ready"] is False
