from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import ExternalConnection


@pytest.mark.django_db
def test_django_connection_validation_becomes_400_and_degraded():
    user = User.objects.create_user(
        username="connection-validation",
        email="connection-validation@example.com",
        password="StrongPass123!",
    )
    connection = ExternalConnection(
        owner=user,
        kind=ExternalConnection.Kind.WORDPRESS,
        name="WordPress",
        base_url="https://example.com",
        username="editor",
        enabled=True,
        health_state=ExternalConnection.Health.HEALTHY,
    )
    connection.set_secret("application-password")
    connection.save()
    client = APIClient()
    client.force_authenticate(user)

    with patch("apps.connections.views.check_wordpress", side_effect=ValidationError("bad credentials")):
        response = client.post(f"/api/v1/connections/{connection.id}/check/", {}, format="json")

    assert response.status_code == 400
    connection.refresh_from_db()
    assert connection.health_state == ExternalConnection.Health.DEGRADED
    assert "bad credentials" in connection.last_error
