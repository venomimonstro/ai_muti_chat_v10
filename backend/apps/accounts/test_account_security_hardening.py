import pytest
from rest_framework.test import APIClient

from apps.b2b_api.keys import issue_key
from apps.b2b_api.models import APIKey, Organization, OrganizationMembership

from .models import User


@pytest.mark.django_db
def test_regular_user_can_start_mfa_setup():
    user = User.objects.create_user(
        username="mfa-user",
        email="mfa-user@example.com",
        password="password123",
    )
    client = APIClient()
    assert client.login(username="mfa-user", password="password123")

    status = client.get("/api/v1/auth/mfa/status/")
    assert status.status_code == 200
    assert status.data["available"] is True
    assert status.data["enabled"] is False

    setup = client.post(
        "/api/v1/auth/mfa/setup/",
        {"password": "password123"},
        format="json",
    )
    assert setup.status_code == 200
    assert setup.data["secret"]
    assert setup.data["otpauth_uri"].startswith("otpauth://totp/")


@pytest.mark.django_db(transaction=True)
def test_account_deletion_deactivates_funded_b2b_access():
    user = User.objects.create_user(
        username="delete-owner",
        email="delete-owner@example.com",
        password="password123",
    )
    organization = Organization.objects.create(
        name="Delete funded org",
        slug="delete-funded-org",
        billing_user=user,
    )
    membership = OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    key, _secret = issue_key(
        organization=organization,
        actor=user,
        name="Must be revoked",
    )

    client = APIClient()
    assert client.login(username="delete-owner", password="password123")
    response = client.post(
        "/api/v1/auth/delete-account/",
        {"password": "password123", "confirmation": "DELETE"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["deleted"] is True

    user.refresh_from_db()
    organization.refresh_from_db()
    membership.refresh_from_db()
    key.refresh_from_db()
    assert user.status == User.Status.DELETED
    assert organization.active is False
    assert membership.status == OrganizationMembership.Status.REMOVED
    assert key.revoked_at is not None
    assert not APIKey.objects.filter(pk=key.pk, revoked_at__isnull=True).exists()


@pytest.mark.django_db
def test_account_export_excludes_b2b_key_secret_material():
    user = User.objects.create_user(
        username="export-owner",
        email="export-owner@example.com",
        password="password123",
    )
    organization = Organization.objects.create(
        name="Export org",
        slug="export-org",
        billing_user=user,
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=OrganizationMembership.Role.OWNER,
    )
    key, secret = issue_key(organization=organization, actor=user, name="Export key")

    client = APIClient()
    client.force_authenticate(user)
    response = client.get("/api/v1/auth/export/")
    assert response.status_code == 200
    serialized = str(response.data)
    assert secret not in serialized
    assert key.secret_hash not in serialized
    assert response.data["api_keys"][0]["prefix"] == key.prefix
