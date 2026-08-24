import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.chat.models import Conversation, Message
from apps.projects.models import Project, ProjectMembership


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
