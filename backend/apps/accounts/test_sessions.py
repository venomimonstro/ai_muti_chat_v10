import pytest
from django.conf import settings
from django.contrib.sessions.models import Session
from rest_framework.test import APIClient

from .models import User
from .session_views import session_public_id


@pytest.mark.django_db
def test_session_list_never_exposes_bearer_session_key():
    user = User.objects.create_user(
        username="session-user",
        email="session-user@example.com",
        password="password123",
    )
    client = APIClient()
    assert client.login(username=user.username, password="password123")
    raw_key = client.cookies[settings.SESSION_COOKIE_NAME].value

    response = client.get("/api/v1/auth/sessions/")

    assert response.status_code == 200
    assert response.data
    current = next(item for item in response.data if item["current"])
    assert "session_key" not in current
    assert current["session_id"] == session_public_id(raw_key)
    assert raw_key not in str(response.data)


@pytest.mark.django_db
def test_opaque_session_id_revokes_only_owned_session():
    user = User.objects.create_user(
        username="session-owner",
        email="session-owner@example.com",
        password="password123",
    )
    first = APIClient()
    second = APIClient()
    assert first.login(username=user.username, password="password123")
    assert second.login(username=user.username, password="password123")
    second_raw_key = second.cookies[settings.SESSION_COOKIE_NAME].value
    second_public_id = session_public_id(second_raw_key)

    response = first.post(f"/api/v1/auth/sessions/{second_public_id}/revoke/")

    assert response.status_code == 204
    assert not Session.objects.filter(session_key=second_raw_key).exists()
    assert second.get("/api/v1/auth/me/").status_code in {401, 403}


@pytest.mark.django_db
def test_unknown_opaque_session_id_does_not_enumerate():
    user = User.objects.create_user(
        username="session-enumeration",
        email="session-enumeration@example.com",
        password="password123",
    )
    client = APIClient()
    assert client.login(username=user.username, password="password123")

    response = client.post("/api/v1/auth/sessions/not-a-real-session/revoke/")

    assert response.status_code == 204
