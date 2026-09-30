import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User

from .models import ExternalConnection
from .vk import check_vk


@pytest.mark.django_db
def test_vk_health_fails_closed_when_selected_group_access_is_lost(monkeypatch):
    user = User.objects.create_user(
        username="vk-health",
        email="vk-health@example.com",
        password="StrongPass123!",
    )
    connection = ExternalConnection(
        owner=user,
        kind=ExternalConnection.Kind.VK,
        name="VK",
        base_url="https://api.vk.com/method",
        username="42",
        enabled=True,
        health_state=ExternalConnection.Health.HEALTHY,
        metadata={"selected_group_id": "777", "selected_group_name": "Business"},
    )
    connection.set_secret("token")
    connection.save()

    def fake_api_call(_token, method, params=None):
        if method == "users.get":
            return {"response": [{"id": 42, "first_name": "Test", "last_name": "User"}]}
        if method == "groups.get":
            return {"response": {"items": [{"id": 888, "name": "Other"}]}}
        raise AssertionError(method)

    monkeypatch.setattr("apps.connections.vk.api_call", fake_api_call)

    with pytest.raises(ValidationError, match="больше недоступно"):
        check_vk(connection)
