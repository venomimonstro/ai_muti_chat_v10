import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.memory_store.models import MemoryItem
from apps.projects.models import Project

from .models import Agent


@pytest.mark.django_db
def test_agent_memory_lists_only_effective_global_and_project_memory():
    user = User.objects.create_user(username="memory-owner", email="memory-owner@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="memory-other", email="memory-other@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=user, name="Brand")
    other_project = Project.objects.create(owner=user, name="Other")
    agent = Agent.objects.create(owner=user, project=project, name="SMM")
    MemoryItem.objects.create(owner=user, scope="global", memory_type="fact", content="Global", normalized_content="global")
    MemoryItem.objects.create(owner=user, project=project, scope="project", memory_type="fact", content="Brand fact", normalized_content="brand fact")
    MemoryItem.objects.create(owner=user, project=other_project, scope="project", memory_type="fact", content="Other project", normalized_content="other project")
    MemoryItem.objects.create(owner=other, scope="global", memory_type="fact", content="Foreign", normalized_content="foreign")

    client = APIClient()
    client.force_authenticate(user)
    response = client.get(f"/api/v1/agents/{agent.id}/memory/")

    assert response.status_code == 200
    contents = {item["content"] for item in response.json()["items"]}
    assert contents == {"Global", "Brand fact"}


@pytest.mark.django_db
def test_agent_memory_create_defaults_to_project_scope_when_agent_has_project():
    user = User.objects.create_user(username="memory-project", email="memory-project@example.com", password="StrongPass123!")
    project = Project.objects.create(owner=user, name="SEO")
    agent = Agent.objects.create(owner=user, project=project, name="SEO Agent")
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        f"/api/v1/agents/{agent.id}/memory/",
        {"content": "Не использовать канцелярит", "memory_type": "instruction"},
        format="json",
    )

    assert response.status_code == 201
    item = MemoryItem.objects.get(pk=response.json()["id"])
    assert item.scope == MemoryItem.Scope.PROJECT
    assert item.project_id == project.id
    assert item.owner_id == user.id
    assert item.pinned is True


@pytest.mark.django_db
def test_agent_memory_edit_and_delete_are_owner_scoped():
    user = User.objects.create_user(username="memory-edit", email="memory-edit@example.com", password="StrongPass123!")
    other = User.objects.create_user(username="memory-edit-other", email="memory-edit-other@example.com", password="StrongPass123!")
    agent = Agent.objects.create(owner=user, name="Agent")
    item = MemoryItem.objects.create(
        owner=user,
        scope=MemoryItem.Scope.GLOBAL,
        memory_type=MemoryItem.Type.FACT,
        content="Old fact",
        normalized_content="old fact",
        pinned=True,
    )
    client = APIClient()
    client.force_authenticate(other)
    denied = client.patch(
        f"/api/v1/agents/{agent.id}/memory/{item.id}/",
        {"content": "Hacked"},
        format="json",
    )
    assert denied.status_code == 404

    client.force_authenticate(user)
    changed = client.patch(
        f"/api/v1/agents/{agent.id}/memory/{item.id}/",
        {"content": "New fact"},
        format="json",
    )
    assert changed.status_code == 200
    item.refresh_from_db()
    assert item.content == "New fact"

    deleted = client.delete(f"/api/v1/agents/{agent.id}/memory/{item.id}/")
    assert deleted.status_code == 204
    item.refresh_from_db()
    assert item.status == MemoryItem.Status.DELETED
    assert item.enabled is False
