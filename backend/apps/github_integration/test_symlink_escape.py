import base64

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.projects.models import Project

from .models import GitHubInstallation, GitHubRepositoryBinding
from .services import read_repository_file


@pytest.fixture
def binding(db):
    user = User.objects.create_user(
        username="github-symlink",
        email="github-symlink@example.test",
        password="password123",
    )
    project = Project.objects.create(owner=user, name="Symlink project")
    installation = GitHubInstallation.objects.create(
        owner=user,
        installation_id=777001,
        account_login="owner",
        permissions={"contents": "read"},
    )
    return GitHubRepositoryBinding.objects.create(
        project=project,
        installation=installation,
        repository_id=777002,
        full_name="owner/private",
        default_branch="main",
    )


@pytest.mark.django_db
def test_read_rejects_symlink_before_following_target(monkeypatch, binding):
    monkeypatch.setattr(
        "apps.github_integration.services.installation_token",
        lambda *_args, **_kwargs: "installation-token",
    )
    calls = []

    def fake_request(method, url, **_kwargs):
        calls.append(url)
        return [
            {
                "name": "safe-name.txt",
                "path": "safe-name.txt",
                "type": "symlink",
                "target": ".env.production",
                "sha": "a" * 40,
            }
        ]

    monkeypatch.setattr("apps.github_integration.services._json_request", fake_request)

    with pytest.raises(ValidationError, match="Symlinks and submodules"):
        read_repository_file(binding, "safe-name.txt")
    assert len(calls) == 1


@pytest.mark.django_db
def test_regular_file_is_read_only_after_parent_entry_validation(monkeypatch, binding):
    monkeypatch.setattr(
        "apps.github_integration.services.installation_token",
        lambda *_args, **_kwargs: "installation-token",
    )
    encoded = base64.b64encode(b"hello").decode("ascii")
    responses = iter(
        [
            [
                {
                    "name": "README.md",
                    "path": "README.md",
                    "type": "file",
                    "sha": "b" * 40,
                }
            ],
            {
                "name": "README.md",
                "path": "README.md",
                "type": "file",
                "sha": "b" * 40,
                "encoding": "base64",
                "content": encoded,
            },
        ]
    )
    monkeypatch.setattr(
        "apps.github_integration.services._json_request",
        lambda *_args, **_kwargs: next(responses),
    )

    result = read_repository_file(binding, "README.md")

    assert result["content"] == "hello"
    assert result["sha"] == "b" * 40
