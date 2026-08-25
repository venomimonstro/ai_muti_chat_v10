import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation, Message
from apps.projects.models import Project, ProjectMembership

from .embeddings import index_message


@pytest.mark.django_db
def test_search_finds_owned_content_without_cross_user_leak():
    user = User.objects.create_user(
        username="search", email="search@example.com", password="password123"
    )
    outsider = User.objects.create_user(
        username="search-other", email="search-other@example.com", password="password123"
    )
    conversation = Conversation.objects.create(owner=user, title="Маркетинговый план")
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Нужно продвижение в Яндексе",
    )
    foreign = Conversation.objects.create(owner=outsider, title="Секретный маркетинг")
    Message.objects.create(
        conversation=foreign,
        role=Message.Role.USER,
        content="Чужая маркетинговая стратегия",
    )
    project = Project.objects.create(owner=user, name="Маркетинг BBTEC")
    ProjectMembership.objects.create(project=project, user=user, role=ProjectMembership.Role.OWNER)
    client = APIClient()
    client.force_authenticate(user)
    response = client.get("/api/v1/search/?q=Маркет")
    assert response.status_code == 200
    titles = {item["title"] for item in response.data["results"]}
    assert "Маркетинговый план" in titles
    assert "Маркетинг BBTEC" in titles
    assert "Секретный маркетинг" not in titles


@pytest.mark.django_db
def test_semantic_history_search_returns_navigation_without_keyword_match():
    user = User.objects.create_user(
        username="semantic", email="semantic@example.com", password="password123"
    )
    outsider = User.objects.create_user(
        username="semantic-other", email="semantic-other@example.com", password="password123"
    )
    conversation = Conversation.objects.create(owner=user, title="Финансовая модель")
    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="Стратегия монетизации сервиса основана на ежемесячной подписке",
    )
    index_message(message)
    foreign = Message.objects.create(
        conversation=Conversation.objects.create(owner=outsider, title="Чужие финансы"),
        role=Message.Role.ASSISTANT,
        content="Монетизация платформы через секретный корпоративный контракт",
    )
    index_message(foreign)
    client = APIClient()
    client.force_authenticate(user)

    response = client.get("/api/v1/search/?q=как+монетизировать+платформу&type=message")

    assert response.status_code == 200
    ids = {item["id"] for item in response.data["results"]}
    assert str(message.id) in ids
    assert str(foreign.id) not in ids
    result = next(item for item in response.data["results"] if item["id"] == str(message.id))
    assert result["match"] in {"semantic", "hybrid"}
    assert result["navigation"] == {
        "conversation_id": str(conversation.id),
        "message_id": str(message.id),
        "anchor": f"message-{message.id}",
    }


@pytest.mark.django_db
def test_search_filters_project_role_and_validates_acl():
    user = User.objects.create_user(
        username="filters", email="filters@example.com", password="password123"
    )
    outsider = User.objects.create_user(
        username="filters-other", email="filters-other@example.com", password="password123"
    )
    project = Project.objects.create(owner=user, name="Allowed")
    other_project = Project.objects.create(owner=outsider, name="Denied")
    conversation = Conversation.objects.create(owner=user, project=project, title="Project chat")
    user_message = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="План продвижения в Яндексе"
    )
    assistant_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="План продвижения включает SEO",
    )
    index_message(user_message)
    index_message(assistant_message)
    client = APIClient()
    client.force_authenticate(user)

    response = client.get(
        f"/api/v1/search/?q=план+продвижения&type=message&project={project.id}&role=user"
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.data["results"]] == [str(user_message.id)]
    denied = client.get(f"/api/v1/search/?q=план&type=message&project={other_project.id}")
    assert denied.status_code == 400
    assert client.get("/api/v1/search/?q=план&project=not-a-uuid").status_code == 400
