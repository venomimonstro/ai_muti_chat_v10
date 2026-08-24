import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User, UserPreference
from apps.ai_registry.adapters import ProviderStreamEvent
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion
from apps.billing.services import credit
from apps.chat.models import Conversation, Generation, Message
from apps.chat.streaming import prepare, run
from apps.projects.models import Project, ProjectMembership

from .models import GenerationMemoryUsage, MemoryItem
from .services import build_memory_context, normalize_content, process_explicit_command


@pytest.fixture
def memory_user():
    user = User.objects.create_user(
        username="memory", email="memory@example.com", password="password123"
    )
    UserPreference.objects.create(user=user)
    return user


@pytest.mark.django_db
def test_memory_crud_is_scoped_and_revisioned(client, memory_user):
    other = User.objects.create_user(
        username="other-memory", email="other-memory@example.com", password="password123"
    )
    client.force_login(memory_user)
    created = client.post(
        "/api/v1/memories/",
        {"scope": "global", "memory_type": "preference", "content": "Отвечай кратко"},
        content_type="application/json",
    )
    assert created.status_code == 201
    item_id = created.json()["id"]
    updated = client.patch(
        f"/api/v1/memories/{item_id}/",
        {"content": "Отвечай кратко и по-русски"},
        content_type="application/json",
    )
    assert updated.status_code == 200
    assert len(updated.json()["revisions"]) == 2

    client.force_login(other)
    assert client.get("/api/v1/memories/").json() == []
    assert client.get(f"/api/v1/memories/{item_id}/").status_code == 404


@pytest.mark.django_db
def test_project_memory_cannot_cross_project_acl(client, memory_user):
    owner = User.objects.create_user(
        username="project-owner", email="project-owner@example.com", password="password123"
    )
    project = Project.objects.create(owner=owner, name="Чужой проект")
    ProjectMembership.objects.create(project=project, user=owner, role=ProjectMembership.Role.OWNER)
    client.force_login(memory_user)
    response = client.post(
        "/api/v1/memories/",
        {"scope": "project", "project": str(project.id), "content": "Чужой секрет"},
        content_type="application/json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_explicit_commands_remember_and_forget(memory_user):
    conversation = Conversation.objects.create(owner=memory_user)
    remember = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="Запомни это: мой язык русский"
    )
    result, suppress = process_explicit_command(
        user=memory_user, conversation=conversation, source_message=remember
    )
    assert result["action"] == "remembered"
    assert suppress is False
    item = MemoryItem.objects.get(owner=memory_user)
    assert item.scope == MemoryItem.Scope.GLOBAL
    assert item.source_message == remember

    forget = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="Забудь мой язык русский"
    )
    result, _ = process_explicit_command(
        user=memory_user, conversation=conversation, source_message=forget
    )
    item.refresh_from_db()
    assert result["action"] == "forgotten"
    assert item.status == MemoryItem.Status.ARCHIVED


@pytest.mark.django_db
def test_memory_scope_and_disable_controls(memory_user):
    project = Project.objects.create(owner=memory_user, name="Alpha")
    ProjectMembership.objects.create(
        project=project, user=memory_user, role=ProjectMembership.Role.OWNER
    )
    in_project = Conversation.objects.create(owner=memory_user, project=project)
    outside = Conversation.objects.create(owner=memory_user)
    MemoryItem.objects.create(
        owner=memory_user,
        project=project,
        scope=MemoryItem.Scope.PROJECT,
        content="Бюджет Alpha 300 000 ₽",
        normalized_content=normalize_content("Бюджет Alpha 300 000 ₽"),
    )
    assert "300 000" in build_memory_context(memory_user, in_project)[0]
    assert "300 000" not in build_memory_context(memory_user, outside)[0]
    in_project.memory_enabled = False
    in_project.save(update_fields=["memory_enabled"])
    assert build_memory_context(memory_user, in_project) == ("", [])

    in_project.memory_enabled = True
    in_project.save(update_fields=["memory_enabled"])
    memory_user.preferences.memory_enabled = False
    memory_user.preferences.save(update_fields=["memory_enabled"])
    assert build_memory_context(memory_user, in_project) == ("", [])


@pytest.mark.django_db
def test_no_memory_command_suppresses_context_for_request(memory_user):
    conversation = Conversation.objects.create(owner=memory_user)
    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Не запоминай: это временный запрос",
    )
    result, suppress = process_explicit_command(
        user=memory_user, conversation=conversation, source_message=message
    )
    assert result["action"] == "ignored"
    assert suppress is True
    assert MemoryItem.objects.filter(owner=memory_user).count() == 0


class CaptureAdapter:
    def __init__(self):
        self.messages = []

    def stream(self, **kwargs):
        self.messages = kwargs["messages"]
        yield ProviderStreamEvent(kind="delta", text_delta="готово")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="memory-ok",
            input_tokens=10,
            output_tokens=2,
        )


@pytest.mark.django_db(transaction=True)
def test_explicit_memory_is_snapshotted_and_used_in_generation(memory_user):
    credit(memory_user, Decimal("10"), "test", "memory-context")
    provider = Provider.objects.create(slug="memory-echo", name="Memory Echo")
    AIModel.objects.create(
        provider=provider,
        slug="echo-v1",
        display_name="Echo",
        upstream_model="echo-v1",
    )
    PriceVersion.objects.create(
        model_slug="echo-v1",
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    conversation = Conversation.objects.create(owner=memory_user)
    generation, _ = prepare(
        user=memory_user,
        conversation=conversation,
        content="Запомни это: предпочитаю короткие ответы",
        client_message_id=uuid.uuid4(),
        idempotency_key="memory:context",
    )
    adapter = CaptureAdapter()
    events = "".join(run(generation, adapter=adapter))
    generation.refresh_from_db()
    assert generation.state == Generation.State.COMPLETED
    assert generation.context_snapshot["memory_action"]["action"] == "remembered"
    assert "предпочитаю короткие ответы" in adapter.messages[0]["content"]
    assert GenerationMemoryUsage.objects.filter(generation=generation).count() == 1
    assert "event: memory" in events
