import pytest
from rest_framework.test import APIClient

from .models import User


@pytest.mark.django_db
def test_account_export_is_user_scoped():
    user = User.objects.create_user(username="export-user", email="export@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="other-user", email="other@example.com", password="StrongPass123!")
    client = APIClient(); client.force_authenticate(user)
    response = client.get("/api/v1/auth/export/")
    assert response.status_code == 200
    assert str(response.data["account"]["id"]) == str(user.id)
    assert response.data["account"]["email"] == user.email
    assert response.data["account"]["email"] != other.email


@pytest.mark.django_db
def test_account_delete_requires_password_and_confirmation():
    user = User.objects.create_user(username="delete-user", email="delete@example.com", password="StrongPass123!")
    client = APIClient(); client.force_authenticate(user)
    denied = client.post("/api/v1/auth/delete-account/", {"password": "StrongPass123!", "confirmation": "NO"}, format="json")
    assert denied.status_code == 400
    user.refresh_from_db(); assert user.status == User.Status.ACTIVE
    accepted = client.post("/api/v1/auth/delete-account/", {"password": "StrongPass123!", "confirmation": "DELETE"}, format="json")
    assert accepted.status_code == 200
    user.refresh_from_db()
    assert user.status == User.Status.DELETED
    assert user.email.endswith("@example.invalid")
    assert user.has_usable_password() is False
