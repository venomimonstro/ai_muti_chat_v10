from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import Notification, User

from .models import ExternalConnection
from .tasks import check_external_connections


def _connection(*, health=ExternalConnection.Health.HEALTHY, username="connection-owner"):
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="StrongPass123!",
    )
    connection = ExternalConnection(
        owner=user,
        kind=ExternalConnection.Kind.WORDPRESS,
        name="Main WordPress",
        base_url="https://example.com",
        username="editor",
        health_state=health,
    )
    connection.set_secret("application-password")
    connection.save()
    return user, connection


@pytest.mark.django_db(transaction=True)
def test_health_watch_notifies_on_degradation_once_and_on_recovery():
    user, connection = _connection()

    with patch(
        "apps.connections.tasks.check_wordpress",
        side_effect=ValidationError("Credentials revoked"),
    ):
        first = check_external_connections.run()
        second = check_external_connections.run()

    connection.refresh_from_db()
    assert first["degraded"] == 1
    assert second["degraded"] == 1
    assert connection.health_state == ExternalConnection.Health.DEGRADED
    assert connection.last_error
    assert Notification.objects.filter(
        user=user,
        title="Проблема с внешним подключением",
    ).count() == 1

    with patch(
        "apps.connections.tasks.check_wordpress",
        return_value={"user_id": 10, "name": "Editor"},
    ):
        recovered = check_external_connections.run()

    connection.refresh_from_db()
    assert recovered["healthy"] == 1
    assert connection.health_state == ExternalConnection.Health.HEALTHY
    assert connection.last_error == ""
    assert connection.metadata["user_id"] == 10
    assert Notification.objects.filter(
        user=user,
        title="Подключение снова работает",
    ).count() == 1


@pytest.mark.django_db(transaction=True)
def test_health_watch_does_not_overwrite_connection_edited_during_network_call():
    _user, connection = _connection(username="connection-race")

    def mutate_while_checking(_connection):
        current = ExternalConnection.objects.get(pk=connection.pk)
        current.name = "Edited while checking"
        current.save(update_fields=["name", "updated_at"])
        return {"user_id": 20, "name": "Remote user"}

    with patch("apps.connections.tasks.check_wordpress", side_effect=mutate_while_checking):
        result = check_external_connections.run()

    connection.refresh_from_db()
    assert result["skipped_changed"] == 1
    assert connection.name == "Edited while checking"
    assert connection.metadata == {}
