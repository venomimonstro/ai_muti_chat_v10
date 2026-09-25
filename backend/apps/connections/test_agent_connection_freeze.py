import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.agents.models import Agent, AgentRun

from .models import AgentConnectionBinding, ExternalConnection


def _subject():
    user = User.objects.create_user(
        username="connection-freeze",
        email="connection-freeze@example.com",
        password="StrongPass123!",
    )
    agent = Agent.objects.create(
        owner=user,
        name="Publisher",
        objective="Publish safely",
        status=Agent.Status.ACTIVE,
    )
    connection = ExternalConnection(
        owner=user,
        kind=ExternalConnection.Kind.WORDPRESS,
        name="Main WordPress",
        base_url="https://example.com",
        username="editor",
    )
    connection.set_secret("application-password")
    connection.save()
    binding = AgentConnectionBinding.objects.create(
        agent=agent,
        connection=connection,
        purpose="publish",
        enabled=True,
    )
    run = AgentRun.objects.create(
        owner=user,
        agent=agent,
        objective="Active publication",
        state=AgentRun.State.RUNNING,
    )
    return user, agent, connection, binding, run


@pytest.mark.django_db
def test_agent_connection_binding_cannot_change_during_active_run():
    user, _agent, _connection, binding, _run = _subject()
    client = APIClient()
    client.force_authenticate(user)

    response = client.patch(
        f"/api/v1/agent-connections/{binding.id}/",
        {"enabled": False},
        format="json",
    )
    assert response.status_code == 400

    deleted = client.delete(f"/api/v1/agent-connections/{binding.id}/")
    assert deleted.status_code == 400

    binding.refresh_from_db()
    assert binding.enabled is True


@pytest.mark.django_db
def test_external_connection_cannot_change_or_delete_while_in_use():
    user, _agent, connection, _binding, _run = _subject()
    client = APIClient()
    client.force_authenticate(user)

    disabled = client.patch(
        f"/api/v1/connections/{connection.id}/",
        {"enabled": False},
        format="json",
    )
    assert disabled.status_code == 400

    deleted = client.delete(f"/api/v1/connections/{connection.id}/")
    assert deleted.status_code == 400

    connection.refresh_from_db()
    assert connection.enabled is True
