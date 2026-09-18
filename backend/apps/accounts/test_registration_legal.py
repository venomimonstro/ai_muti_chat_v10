import pytest
from rest_framework.test import APIClient

from .models import User


@pytest.mark.django_db
def test_registration_requires_legal_acceptance():
    response = APIClient().post(
        "/api/v1/auth/register/",
        {"username": "legal-user", "email": "legal@example.com", "password": "StrongPass123!", "accepted_terms": False},
        format="json",
    )
    assert response.status_code == 400
    assert User.objects.filter(username="legal-user").exists() is False


@pytest.mark.django_db
def test_registration_persists_legal_acceptance():
    response = APIClient().post(
        "/api/v1/auth/register/",
        {"username": "legal-ok", "email": "legal-ok@example.com", "password": "StrongPass123!", "accepted_terms": True},
        format="json",
    )
    assert response.status_code == 201
    user = User.objects.get(username="legal-ok")
    assert user.legal_accepted_at is not None
    assert user.legal_version
