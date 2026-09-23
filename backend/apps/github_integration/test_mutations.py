import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project

from .branching import create_repository_branch
from .models import GitHubInstallation, GitHubOperationLog, GitHubRepositoryBinding
from .mutations import create_repository_file, delete_repository_file


def _binding():
    user = User.objects.create_user(
        username="github-mutation", email="github-mutation@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Mutation project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=8801,
        account_login="mutation-user",
        account_type="User",
        permissions={"contents": "write", "metadata": "read"},
    )
    binding = GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=9901,
        full_name="mutation-user/repo",
        default_branch="main",
        write_enabled=True,
    )
    return user, project, binding


@pytest.mark.django_db
def test_create_file_is_disabled_without_explicit_project_write_access(monkeypatch):
    user = User.objects.create_user(
        username="github-create-off", email="github-create-off@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Read only")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=8802,
        account_login="read-user",
        account_type="User",
        permissions={"contents": "write"},
    )
    binding = GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=9902,
        full_name="read-user/repo",
        write_enabled=False,
    )
    monkeypatch.setattr("apps.github_integration.mutations.installation_token", lambda *args, **kwargs: "never")

    with pytest.raises(ValidationError, match="disabled"):
        create_repository_file(binding, "src/new.py", content="print('x')", message="create")


@pytest.mark.django_db
def test_create_file_never_sends_sha_so_github_rejects_accidental_overwrite(monkeypatch):
    _user, _project, binding = _binding()
    calls = []
    monkeypatch.setattr("apps.github_integration.mutations.installation_token", lambda *args, **kwargs: "token")

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return {"content": {"sha": "new-sha"}, "commit": {"sha": "commit-sha"}}

    monkeypatch.setattr("apps.github_integration.mutations._json_request", fake_request)
    result = create_repository_file(
        binding,
        "src/new.py",
        content="print('safe')\n",
        message="create source",
    )

    assert result["commit_sha"] == "commit-sha"
    assert calls[0][0] == "PUT"
    assert "sha" not in calls[0][2]["json"]
    assert calls[0][2]["json"]["branch"] == "main"


@pytest.mark.django_db
def test_delete_file_requires_expected_sha_and_explicit_confirmation_at_api(monkeypatch):
    user, project, _binding_obj = _binding()
    client = APIClient()
    client.force_authenticate(user)
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "true")

    missing_confirmation = client.post(
        f"/api/v1/projects/{project.id}/github/file/delete/",
        {"path": "README.md", "expected_sha": "a" * 40},
        format="json",
    )
    assert missing_confirmation.status_code == 400

    with pytest.raises(ValidationError, match="Expected file SHA"):
        delete_repository_file(
            project.github_repository,
            "README.md",
            expected_sha="",
            message="delete",
        )


@pytest.mark.django_db
def test_create_api_requires_confirmation_and_records_audit(monkeypatch):
    user, project, _binding_obj = _binding()
    client = APIClient()
    client.force_authenticate(user)
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "true")

    denied = client.post(
        f"/api/v1/projects/{project.id}/github/file/create/",
        {"path": "src/new.py", "content": "print(1)"},
        format="json",
    )
    assert denied.status_code == 400

    monkeypatch.setattr(
        "apps.github_integration.mutation_views.create_repository_file",
        lambda *args, **kwargs: {
            "path": "src/new.py",
            "branch": "main",
            "content_sha": "content-sha",
            "commit_sha": "commit-sha",
        },
    )
    created = client.post(
        f"/api/v1/projects/{project.id}/github/file/create/",
        {
            "path": "src/new.py",
            "content": "print(1)",
            "confirm_write": True,
            "message": "create",
        },
        format="json",
    )
    assert created.status_code == 201
    log = GitHubOperationLog.objects.filter(binding__project=project, action="create_file").latest("created_at")
    assert log.success is True
    assert log.path == "src/new.py"


@pytest.mark.django_db
def test_working_branch_is_created_from_default_branch_without_touching_default(monkeypatch):
    _user, _project, binding = _binding()
    calls = []
    monkeypatch.setattr("apps.github_integration.branching.installation_token", lambda *args, **kwargs: "token")

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET":
            return {"object": {"sha": "base-sha"}}
        return {"ref": "refs/heads/ai-workspace/test", "object": {"sha": "base-sha"}}

    monkeypatch.setattr("apps.github_integration.branching._json_request", fake_request)
    result = create_repository_branch(binding, "ai-workspace/test")

    assert result == {"branch": "ai-workspace/test", "base_branch": "main", "sha": "base-sha"}
    assert calls[0][0] == "GET"
    assert calls[1][0] == "POST"
    assert calls[1][2]["json"] == {"ref": "refs/heads/ai-workspace/test", "sha": "base-sha"}


@pytest.mark.django_db
def test_branch_api_requires_confirmation_and_audits(monkeypatch):
    user, project, _binding_obj = _binding()
    client = APIClient()
    client.force_authenticate(user)
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "true")

    denied = client.post(
        f"/api/v1/projects/{project.id}/github/branches/",
        {"branch": "ai-workspace/no-confirm"},
        format="json",
    )
    assert denied.status_code == 400

    monkeypatch.setattr(
        "apps.github_integration.dev_views.create_repository_branch",
        lambda *args, **kwargs: {"branch": "ai-workspace/work", "base_branch": "main", "sha": "abc"},
    )
    created = client.post(
        f"/api/v1/projects/{project.id}/github/branches/",
        {"branch": "ai-workspace/work", "confirm_create": True},
        format="json",
    )

    assert created.status_code == 200
    log = GitHubOperationLog.objects.filter(binding__project=project, action="create_branch").latest("created_at")
    assert log.success is True
    assert log.branch == "ai-workspace/work"


def test_branch_name_rejects_traversal_and_default_branch():
    class FakeBinding:
        write_enabled = True
        default_branch = "main"

    with pytest.raises(ValidationError):
        create_repository_branch(FakeBinding(), "../evil")
    with pytest.raises(ValidationError):
        create_repository_branch(FakeBinding(), "main")
