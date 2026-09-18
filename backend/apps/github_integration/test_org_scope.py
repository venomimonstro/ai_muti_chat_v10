import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project

from .models import GitHubInstallation, GitHubRepositoryBinding


@pytest.mark.django_db
def test_organization_installation_repository_listing_is_fail_closed(monkeypatch):
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "true")
    user = User.objects.create_user(
        username="github-org-user", email="github-org-user@example.test", password="password123"
    )
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=91001,
        account_login="example-org",
        account_type="Organization",
        permissions={"contents": "write"},
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.get(f"/api/v1/github/installations/{installation.id}/repositories/")

    assert response.status_code == 403
    assert "user-scoped" in str(response.data)


@pytest.mark.django_db
def test_organization_installation_cannot_be_bound_even_if_repository_id_is_known():
    user = User.objects.create_user(
        username="github-org-bind", email="github-org-bind@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Org project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=91002,
        account_login="example-org",
        account_type="Organization",
        permissions={"contents": "write"},
    )

    with pytest.raises(ValidationError, match="user-scoped"):
        GitHubRepositoryBinding.objects.create(
            project=project,
            installation=installation,
            repository_id=123456,
            full_name="example-org/private-repo",
            default_branch="main",
        )
