from urllib.parse import parse_qs, urlparse

import pytest
from django.core import signing
from rest_framework.test import APIClient

from apps.accounts.models import User

from .flow_views import OAUTH_STATE_SALT
from .models import GitHubInstallation
from .views import STATE_SALT


@pytest.mark.django_db
def test_setup_callback_redirects_to_oauth_with_signed_installation(monkeypatch):
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "Iv1.test-client")
    user = User.objects.create_user(
        username="github-flow", email="github-flow@example.test", password="password123"
    )
    client = APIClient()
    client.force_authenticate(user)
    initial_state = signing.dumps({"user_id": str(user.id)}, salt=STATE_SALT, compress=True)

    response = client.get(
        "/api/v1/github/setup/",
        {
            "installation_id": "12345",
            "setup_action": "install",
            "state": initial_state,
        },
    )

    assert response.status_code == 302
    parsed = urlparse(response["Location"])
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"
    oauth_state = parse_qs(parsed.query)["state"][0]
    payload = signing.loads(oauth_state, salt=OAUTH_STATE_SALT, max_age=900)
    assert payload == {"user_id": str(user.id), "installation_id": 12345}


@pytest.mark.django_db
def test_oauth_callback_verifies_installation_before_persisting(monkeypatch):
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "true")
    monkeypatch.setenv("FRONTEND_PUBLIC_URL", "https://ai.example.test")
    user = User.objects.create_user(
        username="github-oauth", email="github-oauth@example.test", password="password123"
    )
    client = APIClient()
    client.force_authenticate(user)
    oauth_state = signing.dumps(
        {"user_id": str(user.id), "installation_id": 22222},
        salt=OAUTH_STATE_SALT,
        compress=True,
    )
    monkeypatch.setattr(
        "apps.github_integration.flow_views.exchange_user_code",
        lambda _code: "temporary-user-token",
    )
    monkeypatch.setattr(
        "apps.github_integration.flow_views.verified_installation",
        lambda token, installation_id: {
            "id": installation_id,
            "account": {"login": "github-oauth", "type": "User"},
            "repository_selection": "selected",
            "permissions": {"contents": "write"},
        }
        if token == "temporary-user-token"
        else None,
    )

    response = client.get(
        "/api/v1/github/callback/",
        {"code": "temporary-code", "state": oauth_state},
    )

    assert response.status_code == 302
    assert response["Location"] == "https://ai.example.test/app/projects?github=connected"
    installation = GitHubInstallation.objects.get(installation_id=22222)
    assert installation.owner_id == user.id
    assert installation.account_login == "github-oauth"
    assert installation.permissions == {"contents": "write"}


@pytest.mark.django_db
def test_oauth_state_cannot_be_reused_by_another_service_user(monkeypatch):
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "true")
    owner = User.objects.create_user(
        username="github-state-owner", email="state-owner@example.test", password="password123"
    )
    attacker = User.objects.create_user(
        username="github-state-attacker", email="state-attacker@example.test", password="password123"
    )
    state = signing.dumps(
        {"user_id": str(owner.id), "installation_id": 33333},
        salt=OAUTH_STATE_SALT,
        compress=True,
    )
    client = APIClient()
    client.force_authenticate(attacker)

    response = client.get("/api/v1/github/callback/", {"code": "x", "state": state})

    assert response.status_code == 400
    assert not GitHubInstallation.objects.filter(installation_id=33333).exists()
