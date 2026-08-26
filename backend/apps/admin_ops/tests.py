import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import SupportRequest, User
from apps.ai_registry.models import AIModel, Provider
from apps.b2b_api.keys import issue_key
from apps.b2b_api.models import Organization, OrganizationMembership

from .models import AdminAuditEvent, BackupRecord, FeatureFlag, ReleaseRecord, SecurityEvent
from .services import feature_enabled


def admin_client(*, platform_role=True):
    user = User.objects.create_user(
        username="admin",
        email="admin@example.com",
        password="password123",
        is_staff=True,
        role=User.Role.PLATFORM_ADMIN if platform_role else User.Role.USER,
    )
    client = APIClient()
    client.force_authenticate(user)
    return user, client


@pytest.mark.django_db
def test_every_admin_control_plane_is_protected_and_available():
    regular = User.objects.create_user(
        username="regular", email="regular@example.com", password="password123"
    )
    client = APIClient()
    client.force_authenticate(regular)
    assert client.get("/api/v1/admin/overview/").status_code == 403

    _admin, client = admin_client(platform_role=True)
    endpoints = [
        "overview",
        "finance",
        "payments",
        "ledger",
        "pricing",
        "quality",
        "incidents",
        "providers",
        "requests",
        "users-organizations",
        "security",
        "releases",
        "backups",
        "support",
        "feature-flags",
        "audit",
    ]
    for endpoint in endpoints:
        response = client.get(f"/api/v1/admin/{endpoint}/")
        assert response.status_code == 200, endpoint


@pytest.mark.django_db
def test_feature_flag_rollout_and_immutable_audit():
    admin, client = admin_client()
    user = User.objects.create_user(
        username="flag-user", email="flag-user@example.com", password="password123"
    )
    created = client.post(
        "/api/v1/admin/feature-flags/",
        {
            "key": "new-chat-ui",
            "enabled": True,
            "rollout_percent": 0,
            "allow_user_ids": [str(user.id)],
        },
        format="json",
    )
    assert created.status_code == 201
    assert feature_enabled("new-chat-ui", user) is True
    assert feature_enabled("new-chat-ui", admin) is False
    updated = client.patch(
        "/api/v1/admin/feature-flags/new-chat-ui/",
        {"rollout_percent": 100, "allow_user_ids": []},
        format="json",
    )
    assert updated.status_code == 200
    assert feature_enabled("new-chat-ui", admin) is True
    assert AdminAuditEvent.objects.filter(action="feature_flag.updated").exists()
    event = AdminAuditEvent.objects.first()
    event.action = "tampered"
    with pytest.raises(ValidationError):
        event.save()
    with pytest.raises(ValidationError):
        event.delete()


@pytest.mark.django_db
def test_mass_model_and_provider_controls_are_scoped_and_audited():
    _admin, client = admin_client()
    first_provider = Provider.objects.create(slug="first", name="First")
    second_provider = Provider.objects.create(slug="second", name="Second")
    first_model = AIModel.objects.create(
        provider=first_provider,
        slug="first-model",
        display_name="First",
        upstream_model="first-model",
    )
    second_model = AIModel.objects.create(
        provider=second_provider,
        slug="second-model",
        display_name="Second",
        upstream_model="second-model",
    )
    response = client.post(
        "/api/v1/admin/providers/bulk-action/",
        {"target": "models", "action": "disable", "ids": [str(first_model.id)]},
        format="json",
    )
    assert response.status_code == 200
    first_model.refresh_from_db()
    second_model.refresh_from_db()
    assert first_model.enabled is False
    assert second_model.enabled is True
    emergency = client.post(
        "/api/v1/admin/providers/bulk-action/",
        {
            "target": "providers",
            "action": "emergency_disable",
            "ids": [str(first_provider.id)],
        },
        format="json",
    )
    assert emergency.status_code == 200
    first_provider.refresh_from_db()
    second_provider.refresh_from_db()
    assert first_provider.emergency_disabled is True
    assert second_provider.emergency_disabled is False
    assert AdminAuditEvent.objects.count() == 2


@pytest.mark.django_db
def test_safe_release_rollout_and_rollback_transitions():
    _admin, client = admin_client()
    created = client.post(
        "/api/v1/admin/releases/",
        {"version": "2026.08.1", "commit_sha": "a" * 40},
        format="json",
    )
    assert created.status_code == 201
    release_id = created.data["id"]
    invalid = client.post(
        f"/api/v1/admin/releases/{release_id}/rollout/",
        {"state": "stable"},
        format="json",
    )
    assert invalid.status_code == 409
    canary = client.post(
        f"/api/v1/admin/releases/{release_id}/rollout/",
        {"state": "canary", "rollout_percent": 5},
        format="json",
    )
    assert canary.status_code == 200
    assert canary.data["rollout_percent"] == 5
    stable = client.post(
        f"/api/v1/admin/releases/{release_id}/rollout/",
        {"state": "stable"},
        format="json",
    )
    assert stable.status_code == 200
    assert stable.data["rollout_percent"] == 100
    rolled_back = client.post(
        f"/api/v1/admin/releases/{release_id}/rollout/",
        {"state": "rolled_back"},
        format="json",
    )
    assert rolled_back.status_code == 200
    assert rolled_back.data["rollout_percent"] == 0
    assert ReleaseRecord.objects.get(pk=release_id).state == ReleaseRecord.State.ROLLED_BACK


@pytest.mark.django_db(transaction=True)
def test_security_containment_blocks_user_and_revokes_keys():
    _admin, client = admin_client()
    user = User.objects.create_user(
        username="abusive", email="abusive@example.com", password="password123"
    )
    organization = Organization.objects.create(
        name="Abusive Org", slug="abusive-org", billing_user=user
    )
    OrganizationMembership.objects.create(
        organization=organization, user=user, role=OrganizationMembership.Role.OWNER
    )
    key, _secret = issue_key(organization=organization, actor=user, name="Compromised")
    created = client.post(
        "/api/v1/admin/security/",
        {
            "category": "abuse",
            "severity": "critical",
            "summary": "Automated abuse detected",
            "user_id": str(user.id),
        },
        format="json",
    )
    assert created.status_code == 201
    contained = client.post(
        f"/api/v1/admin/security/{created.data['id']}/action/",
        {"action": "contain"},
        format="json",
    )
    assert contained.status_code == 200
    user.refresh_from_db()
    key.refresh_from_db()
    assert user.status == User.Status.BLOCKED
    assert key.revoked_at is not None
    assert SecurityEvent.objects.get(pk=created.data["id"]).resolved_by is not None


@pytest.mark.django_db
def test_backup_restore_drill_requires_verified_backup():
    _admin, client = admin_client()
    created = client.post(
        "/api/v1/admin/backups/", {"kind": "full"}, format="json"
    )
    assert created.status_code == 201
    backup_id = created.data["id"]
    premature = client.post(
        f"/api/v1/admin/backups/{backup_id}/action/",
        {"action": "restore_drill"},
        format="json",
    )
    assert premature.status_code == 409
    assert client.post(
        f"/api/v1/admin/backups/{backup_id}/action/",
        {"action": "start"},
        format="json",
    ).status_code == 200
    completed = client.post(
        f"/api/v1/admin/backups/{backup_id}/action/",
        {
            "action": "complete",
            "storage_reference": "s3://private/backups/full.dump",
            "checksum_sha256": "b" * 64,
            "size_bytes": 1024,
        },
        format="json",
    )
    assert completed.status_code == 200
    assert client.post(
        f"/api/v1/admin/backups/{backup_id}/action/",
        {"action": "verify"},
        format="json",
    ).status_code == 200
    restored = client.post(
        f"/api/v1/admin/backups/{backup_id}/action/",
        {"action": "restore_drill"},
        format="json",
    )
    assert restored.status_code == 200
    assert BackupRecord.objects.get(pk=backup_id).status == BackupRecord.Status.RESTORED


@pytest.mark.django_db
def test_support_workflow_is_admin_managed_and_audited():
    _admin, client = admin_client()
    user = User.objects.create_user(
        username="support-user", email="support-user@example.com", password="password123"
    )
    support = SupportRequest.objects.create(user=user, subject="Help", message="Please help")
    response = client.post(
        f"/api/v1/admin/support/{support.id}/status/",
        {"status": SupportRequest.Status.IN_PROGRESS},
        format="json",
    )
    assert response.status_code == 200
    support.refresh_from_db()
    assert support.status == SupportRequest.Status.IN_PROGRESS
    assert AdminAuditEvent.objects.filter(
        action="support.status_changed", target_id=str(support.id)
    ).exists()


@pytest.mark.django_db
def test_invalid_rollout_percent_is_rejected():
    admin, _client = admin_client()
    flag = FeatureFlag(key="invalid", rollout_percent=101, updated_by=admin)
    with pytest.raises(ValidationError):
        flag.full_clean()
