import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation

from .models import Project, ProjectMembership


@pytest.mark.django_db
def test_project_instruction_is_versioned():
    user = User.objects.create_user(username="owner", password="password123")
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        "/api/v1/projects/",
        {"name": "Запуск", "description": "MVP", "instruction": "Версия 1"},
        format="json",
    )
    assert response.status_code == 201
    project = Project.objects.get(pk=response.data["id"])
    assert ProjectMembership.objects.filter(
        project=project, user=user, role=ProjectMembership.Role.OWNER
    ).exists()
    response = client.patch(
        f"/api/v1/projects/{project.id}/", {"instruction": "Версия 2"}, format="json"
    )
    assert response.status_code == 200
    assert list(
        project.instructions.order_by("version").values_list("version", "content", "active")
    ) == [(1, "Версия 1", False), (2, "Версия 2", True)]


@pytest.mark.django_db
def test_project_and_chat_are_isolated_between_users():
    owner = User.objects.create_user(
        username="project-owner", email="project-owner@example.com", password="password123"
    )
    outsider = User.objects.create_user(
        username="outsider", email="project-outsider@example.com", password="password123"
    )
    project = Project.objects.create(owner=owner, name="Секретный проект")
    ProjectMembership.objects.create(project=project, user=owner, role=ProjectMembership.Role.OWNER)
    client = APIClient()
    client.force_authenticate(outsider)
    assert client.get(f"/api/v1/projects/{project.id}/").status_code == 404
    response = client.post(
        "/api/v1/conversations/",
        {"title": "Чужой", "project": str(project.id)},
        format="json",
    )
    assert response.status_code == 400
    assert Conversation.objects.filter(owner=outsider, project=project).exists() is False


@pytest.mark.django_db
def test_viewer_cannot_modify_project():
    owner = User.objects.create_user(
        username="sharing-owner", email="sharing-owner@example.com", password="password123"
    )
    viewer = User.objects.create_user(
        username="viewer", email="viewer@example.com", password="password123"
    )
    project = Project.objects.create(owner=owner, name="Shared")
    ProjectMembership.objects.create(
        project=project, user=viewer, role=ProjectMembership.Role.VIEWER
    )
    client = APIClient()
    client.force_authenticate(viewer)
    assert client.get(f"/api/v1/projects/{project.id}/").status_code == 200
    assert (
        client.patch(
            f"/api/v1/projects/{project.id}/", {"name": "Changed"}, format="json"
        ).status_code
        == 404
    )
    project.refresh_from_db()
    assert project.name == "Shared"
