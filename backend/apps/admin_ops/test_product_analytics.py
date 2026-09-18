import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .analytics_models import ProductEvent


@pytest.mark.django_db
def test_product_event_ingestion_is_allowlisted_and_idempotent():
    client = APIClient()
    payload = {
        "event_name": "landing_view",
        "client_event_id": "event-1",
        "source_path": "/",
        "metadata": {"utm_source": "test"},
    }
    assert client.post("/api/v1/analytics/events/", payload, format="json").status_code == 204
    assert client.post("/api/v1/analytics/events/", payload, format="json").status_code == 204
    assert ProductEvent.objects.count() == 1
    denied = client.post(
        "/api/v1/analytics/events/",
        {"event_name": "arbitrary_event", "client_event_id": "event-2"},
        format="json",
    )
    assert denied.status_code == 400


@pytest.mark.django_db
def test_product_analytics_requires_platform_admin():
    user = User.objects.create_user(
        username="analytics-user", email="analytics-user@example.com", password="StrongPass123!"
    )
    client = APIClient(); client.force_authenticate(user)
    assert client.get("/api/v1/admin/analytics/").status_code == 403


@pytest.mark.django_db
def test_product_analytics_returns_funnel_for_platform_admin(settings):
    settings.ADMIN_MFA_ENFORCED = False
    admin = User.objects.create_user(
        username="analytics-admin",
        email="analytics-admin@example.com",
        password="StrongPass123!",
        role=User.Role.PLATFORM_ADMIN,
        is_staff=True,
    )
    for index, name in enumerate(("landing_view", "register_complete", "email_verified", "first_chat", "payment_success")):
        ProductEvent.objects.create(event_name=name, client_event_id=f"funnel-{index}")
    client = APIClient(); client.force_authenticate(admin)
    response = client.get("/api/v1/admin/analytics/?days=30")
    assert response.status_code == 200
    assert response.data["funnel"]["landing"] == 1
    assert response.data["funnel"]["paid"] == 1
    assert response.data["funnel"]["rates_percent"]["landing_to_registration"] == 100.0
