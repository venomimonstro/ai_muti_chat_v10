from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.admin_ops.models import BackupRecord
from apps.ai_registry.models import AIModel, ModelVersion, Provider
from apps.billing.models import PriceVersion


@pytest.fixture
def admin_client(settings):
    settings.ADMIN_MFA_ENFORCED = False
    admin = User.objects.create_user(
        username="safe-admin",
        email="safe-admin@example.test",
        password="password123!",
        role=User.Role.PLATFORM_ADMIN,
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(admin)
    return admin, client


@pytest.mark.django_db
def test_model_activation_is_blocked_until_provider_version_and_price_are_ready(admin_client, monkeypatch):
    _admin, client = admin_client
    provider = Provider.objects.create(
        slug="safe-provider",
        name="Safe Provider",
        enabled=False,
        health_state=Provider.HealthState.UNKNOWN,
        credential_env="SAFE_PROVIDER_KEY",
    )
    model = AIModel.objects.create(
        provider=provider,
        slug="safe-model",
        display_name="Safe Model",
        upstream_model="",
        enabled=False,
    )

    response = client.post(
        "/api/v1/admin/providers/bulk-action/",
        {"target": "models", "action": "enable", "ids": [str(model.id)]},
        format="json",
    )
    assert response.status_code == 409
    model.refresh_from_db()
    assert model.enabled is False

    monkeypatch.setenv("SAFE_PROVIDER_KEY", "configured-secret")
    provider.enabled = True
    provider.health_state = Provider.HealthState.HEALTHY
    provider.save(update_fields=["enabled", "health_state"])
    model.upstream_model = "provider-model-v1"
    model.save(update_fields=["upstream_model"])
    version = ModelVersion.objects.create(
        model=model,
        version="v1",
        exact_api_id="provider-model-v1",
        stage=ModelVersion.Stage.ACTIVE,
    )
    model.current_version = version
    model.save(update_fields=["current_version"])
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        effective_from=timezone.now(),
    )

    response = client.post(
        "/api/v1/admin/providers/bulk-action/",
        {"target": "models", "action": "enable", "ids": [str(model.id)]},
        format="json",
    )
    assert response.status_code == 200
    model.refresh_from_db()
    assert model.enabled is True


@pytest.mark.django_db
def test_restore_drill_cannot_be_marked_manually(admin_client):
    admin, client = admin_client
    backup = BackupRecord.objects.create(
        kind=BackupRecord.Kind.DATABASE,
        status=BackupRecord.Status.VERIFIED,
        storage_reference="/backups/example.dump",
        checksum_sha256="a" * 64,
        requested_by=admin,
        completed_at=timezone.now(),
        verified_at=timezone.now(),
    )
    response = client.post(
        f"/api/v1/admin/backups/{backup.id}/action/",
        {"action": "restore_drill"},
        format="json",
    )
    assert response.status_code == 409
    backup.refresh_from_db()
    assert backup.status == BackupRecord.Status.VERIFIED
    assert backup.restored_at is None
