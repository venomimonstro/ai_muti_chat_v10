from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion
from apps.billing.services import credit

from .keys import authenticate_raw_key, issue_key
from .models import APIKey, APIUsage, Organization, OrganizationMembership


def registry():
    provider = Provider.objects.create(slug="b2b-echo", name="B2B Echo")
    model = AIModel.objects.create(
        provider=provider,
        slug="echo-b2b",
        display_name="Echo B2B",
        upstream_model="echo-b2b",
        max_output_tokens=128,
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    return model


def account(username="owner"):
    user = User.objects.create_user(
        username=username, email=f"{username}@example.com", password="password123"
    )
    credit(user, Decimal("100"), "test", username)
    organization = Organization.objects.create(
        name="Example", slug=f"example-{username}", billing_user=user
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    key, secret = issue_key(organization=organization, actor=user, name="Production")
    return user, organization, key, secret


@pytest.mark.django_db(transaction=True)
def test_management_key_secret_is_shown_once_and_only_hash_is_stored():
    user, organization, _key, _secret = account()
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/organizations/{organization.id}/keys/",
        {"name": "CI", "allowed_models": ["echo-b2b"]},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["secret"].startswith("aw_live_")
    stored = APIKey.objects.get(pk=response.data["id"])
    assert response.data["secret"] != stored.secret_hash
    listing = client.get(f"/api/v1/organizations/{organization.id}/keys/")
    assert "secret" not in listing.data[0]
    assert authenticate_raw_key(response.data["secret"]).id == stored.id


@pytest.mark.django_db(transaction=True)
def test_openai_compatible_completion_billing_and_idempotency():
    registry()
    user, _organization, _key, secret = account("completion")
    client = APIClient()
    payload = {
        "model": "echo-b2b",
        "messages": [{"role": "user", "content": "Привет API"}],
        "max_completion_tokens": 32,
    }
    headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}", "HTTP_IDEMPOTENCY_KEY": "same"}
    first = client.post("/v1/chat/completions", payload, format="json", **headers)
    second = client.post("/v1/chat/completions", payload, format="json", **headers)
    assert first.status_code == second.status_code == 200
    assert first.data["id"] == second.data["id"]
    assert first.data["object"] == "chat.completion"
    assert first.data["choices"][0]["message"]["role"] == "assistant"
    assert first.data["usage"]["total_tokens"] > 0
    assert APIUsage.objects.count() == 1
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub < Decimal("100")


@pytest.mark.django_db(transaction=True)
def test_stream_models_and_usage_contracts():
    registry()
    _user, _organization, _key, secret = account("stream")
    client = APIClient()
    auth = {"HTTP_AUTHORIZATION": f"Bearer {secret}"}
    models = client.get("/v1/models", **auth)
    assert models.status_code == 200
    assert models.data["object"] == "list"
    response = client.post(
        "/v1/chat/completions",
        {
            "model": "echo-b2b",
            "messages": [{"role": "user", "content": "Поток"}],
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        format="json",
        **auth,
    )
    body = b"".join(response.streaming_content).decode()
    assert response.status_code == 200
    assert "chat.completion.chunk" in body
    assert "[DONE]" in body
    assert '"usage"' in body
    usage = client.get("/v1/usage", **auth)
    assert usage.status_code == 200
    assert usage.data["requests"] == 1
    assert usage.data["models"][0]["model"] == "echo-b2b"


@pytest.mark.django_db(transaction=True)
def test_key_model_budget_rate_and_ip_limits_are_enforced():
    registry()
    _user, organization, key, secret = account("limits")
    key.allowed_models = ["another-model"]
    key.save(update_fields=["allowed_models"])
    client = APIClient()
    auth = {"HTTP_AUTHORIZATION": f"Bearer {secret}"}
    payload = {"model": "echo-b2b", "messages": [{"role": "user", "content": "test"}]}
    denied = client.post("/v1/chat/completions", payload, format="json", **auth)
    assert denied.status_code == 400
    assert denied.data["error"]["code"] == "model_not_allowed"

    key.allowed_models = []
    key.monthly_limit_rub = Decimal("0.0001")
    key.save(update_fields=["allowed_models", "monthly_limit_rub"])
    budget = client.post("/v1/chat/completions", payload, format="json", **auth)
    assert budget.status_code == 402
    assert budget.data["error"]["code"] == "budget_exceeded"

    key.monthly_limit_rub = None
    organization.monthly_limit_rub = Decimal("0.0001")
    organization.save(update_fields=["monthly_limit_rub"])
    key.save(update_fields=["monthly_limit_rub"])
    org_budget = client.post("/v1/chat/completions", payload, format="json", **auth)
    assert org_budget.status_code == 402
    assert org_budget.data["error"]["code"] == "budget_exceeded"

    organization.monthly_limit_rub = None
    organization.save(update_fields=["monthly_limit_rub"])
    key.rate_limit_per_minute = 1
    key.save(update_fields=["rate_limit_per_minute"])
    accepted = client.post("/v1/chat/completions", payload, format="json", **auth)
    assert accepted.status_code == 200
    rate_limited = client.post("/v1/chat/completions", payload, format="json", **auth)
    assert rate_limited.status_code == 429
    assert rate_limited.data["error"]["code"] == "rate_limit_exceeded"

    key.ip_allowlist = ["192.0.2.0/24"]
    key.save(update_fields=["ip_allowlist"])
    ip_denied = client.get("/v1/models", **auth)
    assert ip_denied.status_code == 401
    assert ip_denied.data["error"]["code"] == "ip_not_allowed"
    assert APIUsage.objects.filter(organization=organization).count() == 1


@pytest.mark.django_db(transaction=True)
def test_membership_permissions_prevent_cross_organization_key_access():
    owner, organization, _key, _secret = account("acl-owner")
    outsider = User.objects.create_user(
        username="acl-outsider", email="acl-outsider@example.com", password="password123"
    )
    client = APIClient()
    client.force_authenticate(outsider)
    assert client.get(f"/api/v1/organizations/{organization.id}/keys/").status_code == 404
    assert (
        client.post(
            f"/api/v1/organizations/{organization.id}/keys/",
            {"name": "stolen"},
            format="json",
        ).status_code
        == 404
    )
    client.force_authenticate(owner)
    assert client.get(f"/api/v1/organizations/{organization.id}/keys/").status_code == 200


@pytest.mark.django_db(transaction=True)
def test_revoked_key_returns_openai_error_shape():
    registry()
    _user, _organization, key, secret = account("revoked")
    key.revoked_at = timezone.now()
    key.save(update_fields=["revoked_at"])
    response = APIClient().get(
        "/v1/models", HTTP_AUTHORIZATION=f"Bearer {secret}"
    )
    assert response.status_code == 401
    assert set(response.data["error"]) == {"message", "type", "param", "code"}
    assert response.data["error"]["code"] == "invalid_api_key"
