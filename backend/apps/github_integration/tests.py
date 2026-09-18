import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project, ProjectMembership

from .models import GitHubInstallation, GitHubRepositoryBinding
from .services import _safe_path, write_repository_file


@pytest.mark.parametrize("path", ["../secrets.env", "a/../b.py", "a//b.py", ".", "", "a/./b.py"])
def test_repository_path_traversal_is_rejected(path):
    with pytest.raises(ValidationError):
        _safe_path(path)


@pytest.mark.django_db
def test_write_is_disabled_by_default_and_requires_no_remote_call():
    user = User.objects.create_user(
        username="github-safe", email="github-safe@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Safe project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=1001,
        account_login="safe-user",
    )
    binding = GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=2002,
        full_name="safe-user/private-repo",
        default_branch="main",
    )

    with pytest.raises(ValidationError, match="disabled"):
        write_repository_file(
            binding,
            "README.md",
            content="changed",
            expected_sha="a" * 40,
            message="update",
        )


@pytest.mark.django_db
def test_binding_cannot_enable_write_without_github_contents_write_permission():
    user = User.objects.create_user(
        username="github-readonly", email="github-readonly@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Readonly project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=1101,
        account_login="readonly-user",
        permissions={"contents": "read", "metadata": "read"},
    )
    binding = GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=2202,
        full_name="readonly-user/private-repo",
        default_branch="main",
    )
    binding.write_enabled = True

    with pytest.raises(ValidationError, match="contents: write"):
        binding.save(update_fields=["write_enabled", "updated_at"])


@pytest.mark.django_db
def test_binding_may_enable_write_only_when_github_app_granted_contents_write():
    user = User.objects.create_user(
        username="github-writer", email="github-writer@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Writable project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=1201,
        account_login="writer-user",
        permissions={"contents": "write", "metadata": "read"},
    )
    binding = GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=2302,
        full_name="writer-user/private-repo",
        default_branch="main",
    )
    binding.write_enabled = True
    binding.save(update_fields=["write_enabled", "updated_at"])
    binding.refresh_from_db()
    assert binding.write_enabled is True


@pytest.mark.django_db
def test_project_editor_cannot_use_owner_private_github_repository():
    owner = User.objects.create_user(
        username="github-owner", email="github-owner@example.test", password="password123"
    )
    editor = User.objects.create_user(
        username="github-editor", email="github-editor@example.test", password="password123"
    )
    project = Project.objects.create(owner=owner, name="Private project")
    ProjectMembership.objects.create(
        project=project, user=editor, role=ProjectMembership.Role.EDITOR
    )
    installation = GitHubInstallation.objects.create(
        owner=owner,
        installation_id=3003,
        account_login="owner",
        permissions={"contents": "write"},
    )
    GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=4004,
        full_name="owner/private",
    )
    client = APIClient()
    client.force_authenticate(editor)

    assert client.get(f"/api/v1/projects/{project.id}/github/").status_code == 404
    assert client.get(f"/api/v1/projects/{project.id}/github/tree/").status_code == 404
    assert client.get(
        f"/api/v1/projects/{project.id}/github/file/", {"path": "README.md"}
    ).status_code == 404
    assert client.patch(
        f"/api/v1/projects/{project.id}/github/",
        {"write_enabled": True},
        format="json",
    ).status_code == 404


@pytest.mark.django_db
def test_callback_cannot_take_over_installation_owned_by_another_service_user(monkeypatch):
    owner = User.objects.create_user(
        username="install-owner", email="install-owner@example.test", password="password123"
    )
    attacker = User.objects.create_user(
        username="install-attacker", email="install-attacker@example.test", password="password123"
    )
    GitHubInstallation.objects.create(
        owner=owner,
        installation_id=5555,
        account_login="shared-org",
    )
    monkeypatch.setattr("apps.github_integration.views.exchange_user_code", lambda _code: "token")
    monkeypatch.setattr(
        "apps.github_integration.views.verified_installation",
        lambda _token, _installation_id: {
            "id": 5555,
            "account": {"login": "shared-org", "type": "Organization"},
            "repository_selection": "selected",
            "permissions": {"contents": "read"},
        },
    )
    client = APIClient()
    client.force_authenticate(attacker)

    from django.core import signing
    from .views import STATE_SALT

    state = signing.dumps({"user_id": str(attacker.id)}, salt=STATE_SALT, compress=True)
    response = client.get(
        "/api/v1/github/callback/",
        {"code": "verified-code", "state": state, "installation_id": "5555"},
    )
    assert response.status_code == 400
    assert "другому аккаунту" in str(response.data)
    assert GitHubInstallation.objects.get(installation_id=5555).owner_id == owner.id


@pytest.mark.django_db
def test_callback_can_resolve_single_verified_installation_without_installation_id(monkeypatch):
    user = User.objects.create_user(
        username="install-single", email="install-single@example.test", password="password123"
    )
    monkeypatch.setattr("apps.github_integration.views.exchange_user_code", lambda _code: "token")
    monkeypatch.setattr(
        "apps.github_integration.views.user_installations",
        lambda _token: [
            {
                "id": 7777,
                "account": {"login": "single-user", "type": "User"},
                "repository_selection": "selected",
                "permissions": {"contents": "read"},
            }
        ],
    )
    client = APIClient()
    client.force_authenticate(user)

    from django.core import signing
    from .views import STATE_SALT

    state = signing.dumps({"user_id": str(user.id)}, salt=STATE_SALT, compress=True)
    response = client.get(
        "/api/v1/github/callback/",
        {"code": "verified-code", "state": state},
    )
    assert response.status_code == 200
    record = GitHubInstallation.objects.get(installation_id=7777)
    assert record.owner_id == user.id
    assert record.account_login == "single-user"
    assert record.permissions == {"contents": "read"}


@pytest.mark.django_db
def test_callback_without_installation_id_refuses_ambiguous_installations(monkeypatch):
    user = User.objects.create_user(
        username="install-ambiguous", email="install-ambiguous@example.test", password="password123"
    )
    monkeypatch.setattr("apps.github_integration.views.exchange_user_code", lambda _code: "token")
    monkeypatch.setattr(
        "apps.github_integration.views.user_installations",
        lambda _token: [
            {"id": 8001, "account": {"login": "one"}},
            {"id": 8002, "account": {"login": "two"}},
        ],
    )
    client = APIClient()
    client.force_authenticate(user)

    from django.core import signing
    from .views import STATE_SALT

    state = signing.dumps({"user_id": str(user.id)}, salt=STATE_SALT, compress=True)
    response = client.get(
        "/api/v1/github/callback/",
        {"code": "verified-code", "state": state},
    )
    assert response.status_code == 400
    assert "однозначно" in str(response.data)
    assert not GitHubInstallation.objects.filter(owner=user).exists()


@pytest.mark.django_db
def test_installation_model_has_no_persistent_user_access_token_field():
    field_names = {field.name for field in GitHubInstallation._meta.fields}
    assert "access_token" not in field_names
    assert "user_token" not in field_names
    assert "pat" not in field_names
