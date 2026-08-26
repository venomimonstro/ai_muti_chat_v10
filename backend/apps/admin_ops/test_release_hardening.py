from io import StringIO

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import AdminAuditEvent, ComplianceSignoff, StatusIncident


def staff_client():
    user = User.objects.create_user(
        username="release-admin",
        email="release-admin@example.com",
        password="password123",
        is_staff=True,
    )
    client = APIClient()
    client.force_authenticate(user)
    return user, client


@pytest.mark.django_db
def test_public_status_page_and_security_headers():
    response = APIClient().get("/api/v1/status/")
    assert response.status_code == 200
    assert response.data["status"] == "operational"
    assert {item["name"] for item in response.data["components"]} == {
        "API",
        "База данных",
        "AI-провайдеры",
        "Очередь задач",
    }
    assert "frame-ancestors 'none'" in response["Content-Security-Policy"]
    assert response["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


@pytest.mark.django_db
def test_status_incident_publication_and_resolution_are_audited():
    _user, client = staff_client()
    created = client.post(
        "/api/v1/admin/status-incidents/",
        {
            "title": "Задержки ответов",
            "message": "Исследуем увеличение времени ответа.",
            "impact": "major",
            "affected_components": ["AI-провайдеры"],
        },
        format="json",
    )
    assert created.status_code == 201
    public = APIClient().get("/api/v1/status/")
    assert public.data["status"] == "degraded"
    assert public.data["incidents"][0]["title"] == "Задержки ответов"
    resolved = client.post(
        f"/api/v1/admin/status-incidents/{created.data['id']}/",
        {"state": "resolved", "message": "Работа восстановлена."},
        format="json",
    )
    assert resolved.status_code == 200
    assert StatusIncident.objects.get(pk=created.data["id"]).resolved_at is not None
    assert AdminAuditEvent.objects.filter(action="status_incident.updated").exists()


@pytest.mark.django_db
def test_compliance_signoff_requires_evidence_and_remains_explicit():
    user, client = staff_client()
    rejected = client.post(
        "/api/v1/admin/signoffs/",
        {"key": "privacy-data-flow", "status": "approved"},
        format="json",
    )
    assert rejected.status_code == 400
    approved = client.post(
        "/api/v1/admin/signoffs/",
        {
            "key": "privacy-data-flow",
            "status": "approved",
            "evidence_reference": "legal/review-2026-08-26.pdf",
        },
        format="json",
    )
    assert approved.status_code == 200
    item = ComplianceSignoff.objects.get(pk="privacy-data-flow")
    assert item.reviewed_by == user
    assert item.reviewed_at is not None


@pytest.mark.django_db
def test_prelaunch_gate_validates_structural_controls():
    output = StringIO()
    call_command("prelaunch_check", stdout=output)
    assert "Pre-launch checks passed" in output.getvalue()
