import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider, ProviderApiKey

from .issue_models import SystemIssue


@pytest.mark.django_db
@override_settings(ADMIN_MFA_ENFORCED=False)
def test_admin_can_create_safe_diagnostics_share_link_and_public_reader_can_open_it():
    admin = User.objects.create_user(
        username="diagnostics-admin",
        email="diagnostics-admin@example.test",
        password="password123",
        is_staff=True,
        status=User.Status.ACTIVE,
    )
    provider = Provider.objects.create(
        slug="diagnostics-provider",
        name="Diagnostics Provider",
        enabled=True,
        health_state=Provider.HealthState.DEGRADED,
    )
    key = ProviderApiKey(
        provider=provider,
        label="primary",
        enabled=True,
        health_state=ProviderApiKey.HealthState.DEGRADED,
    )
    key.set_secret("must-never-appear-in-report")
    key.last_error_code = "authentication_error"
    key.save()
    AIModel.objects.create(
        provider=provider,
        slug="diagnostics-model",
        display_name="Diagnostics model",
        upstream_model="diagnostics-upstream",
        enabled=True,
    )
    now = timezone.now()
    SystemIssue.objects.create(
        fingerprint="diag-share-test",
        status=SystemIssue.Status.OPEN,
        severity="critical",
        exception_type="RuntimeError",
        summary="Provider route failed",
        source="backend:test",
        first_seen_at=now,
        last_seen_at=now,
        sample_traceback="secret traceback body",
    )

    client = APIClient()
    client.force_authenticate(admin)
    created = client.post("/api/v1/admin/chat-diagnostics/share/", {}, format="json")

    assert created.status_code == 200
    assert created.data["url"]
    assert created.data["expires_in_seconds"] == 21600

    public = APIClient()
    path = created.data["url"].split("testserver", 1)[-1]
    response = public.get(path)

    assert response.status_code == 200
    payload = response.data
    assert payload["schema_version"] == 5
    assert payload["summary"]["open_system_issues"] >= 1
    assert "chat_readiness" in payload
    assert "stuck_generations" in payload["chat_readiness"]
    assert "stale_customer_reservations" in payload["chat_readiness"]
    assert "models" in payload
    provider_row = next(item for item in payload["providers"] if item["provider"] == provider.slug)
    assert provider_row["key_error_codes"]["authentication_error"] == 1
    assert provider_row["customer_ready"] is False
    model_row = next(item for item in payload["models"] if item["model"] == "diagnostics-model")
    assert model_row["provider"] == provider.slug
    assert model_row["ready"] is False
    assert "provider_unavailable" in model_row["reasons"]
    assert "active_version_metadata_missing" in model_row["warnings"]
    assert "active_version_missing" not in model_row["reasons"]
    serialized = str(payload)
    assert "must-never-appear-in-report" not in serialized
    assert "secret traceback body" not in serialized
    assert payload["privacy"]["api_keys"] is False
    assert payload["privacy"]["tracebacks"] is False
    assert payload["privacy"]["raw_release_logs"] is False
