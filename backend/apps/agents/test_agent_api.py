import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Agent


@pytest.mark.django_db
def test_agent_api_is_owner_scoped_and_template_creation_is_private():
    owner = User.objects.create_user(username="agent-owner", email="agent-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="agent-other", email="agent-other@example.com", password="StrongPass123!")
    Agent.objects.create(owner=other, name="Чужой агент", role="private")

    client = APIClient()
    client.force_authenticate(owner)

    response = client.get("/api/v1/agents/")
    assert response.status_code == 200
    assert response.json() == []

    created = client.post(
        "/api/v1/agents/from-template/",
        {"template": "smm-specialist", "name": "Мой SMM"},
        format="json",
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Мой SMM"
    assert Agent.objects.filter(owner=owner, name="Мой SMM").exists()
    assert not Agent.objects.filter(owner=owner, name="Чужой агент").exists()


@pytest.mark.django_db
def test_agent_run_requires_activation():
    user = User.objects.create_user(username="agent-runner", email="agent-runner@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Разработчик", objective="Исправлять проект")
    client = APIClient()
    client.force_authenticate(user)

    blocked = client.post(f"/api/v1/agents/{agent.id}/run/", {}, format="json")
    assert blocked.status_code == 400

    activated = client.post(f"/api/v1/agents/{agent.id}/activate/", {}, format="json")
    assert activated.status_code == 200

    started = client.post(f"/api/v1/agents/{agent.id}/run/", {"objective": "Провести аудит"}, format="json")
    assert started.status_code == 201
    assert started.json()["state"] == "queued"
