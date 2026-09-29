import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import Provider, ProviderApiKey

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
    SystemIssue.objects.create(
        fingerprint="diag-share-test",
        status=SystemIssue.Status.OPEN,
        severity="critical",
        exception_type="RuntimeError",
        summary="Provider route failed",
        source="backend:test",
        first_seen_at=provider.created_at,
        last_seen_at=provider.created_at,
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
    assert payload["schema_version"] == 2
    assert payload["summary"]["open_system_issues"] >= 1
    assert payload["providers"][0]["key_error_codes"]["authentication_error"] == 1
    serialized = str(payload)
    assert "must-never-appear-in-report" not in serialized
    assert "secret traceback body" not in serialized
    assert payload["privacy"]["api_keys"] is False
    assert payload["privacy"]["tracebacks"] is False
