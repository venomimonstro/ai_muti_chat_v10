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

from .models import GenerationMemoryUsage, MemoryCandidate, MemoryItem
from .services import (
    accept_candidate,
    build_memory_context,
    extract_memory_candidates,
    normalize_content,
    process_explicit_command,
)


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


@pytest.mark.django_db
def test_auto_memory_requires_platform_flag_and_user_opt_in(memory_user, settings):
    conversation = Conversation.objects.create(owner=memory_user)
    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Я предпочитаю короткие ответы.",
    )
    settings.AUTO_MEMORY_ENABLED = False
    memory_user.preferences.auto_memory_enabled = True
    memory_user.preferences.save(update_fields=["auto_memory_enabled"])
    assert (
        extract_memory_candidates(
            user=memory_user, conversation=conversation, source_message=message
        )
        == []
    )

    settings.AUTO_MEMORY_ENABLED = True
    memory_user.preferences.auto_memory_enabled = False
    memory_user.preferences.save(update_fields=["auto_memory_enabled"])
    assert (
        extract_memory_candidates(
            user=memory_user, conversation=conversation, source_message=message
        )
        == []
    )
    assert MemoryCandidate.objects.count() == 0


@pytest.mark.django_db
def test_auto_memory_rejects_sensitive_and_non_user_sources(memory_user, settings):
    settings.AUTO_MEMORY_ENABLED = True
    memory_user.preferences.auto_memory_enabled = True
    memory_user.preferences.save(update_fields=["auto_memory_enabled"])
    conversation = Conversation.objects.create(owner=memory_user)
    secret = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Я использую API key sk-secret-value для проекта.",
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="Я предпочитаю сохранять этот ответ.",
    )
    assert (
        extract_memory_candidates(
            user=memory_user, conversation=conversation, source_message=secret
        )
        == []
    )
    assert (
        extract_memory_candidates(
            user=memory_user, conversation=conversation, source_message=assistant
        )
        == []
    )
    assert MemoryCandidate.objects.count() == 0


@pytest.mark.django_db
def test_candidate_deduplication_and_conflict_resolution(memory_user, settings):
    settings.AUTO_MEMORY_ENABLED = True
    preference = memory_user.preferences
    preference.auto_memory_enabled = True
    preference.auto_memory_default_scope = UserPreference.AutoMemoryScope.GLOBAL
    preference.save(update_fields=["auto_memory_enabled", "auto_memory_default_scope"])
    conversation = Conversation.objects.create(owner=memory_user)
    old_content = "Мой бюджет проекта 100 000 ₽."
    old = MemoryItem.objects.create(
        owner=memory_user,
        scope=MemoryItem.Scope.GLOBAL,
        memory_type=MemoryItem.Type.FACT,
        content=old_content,
        normalized_content=normalize_content(old_content),
        subject_key="fact:budget",
    )
    duplicate_message = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content=old_content
    )
    duplicate = extract_memory_candidates(
        user=memory_user, conversation=conversation, source_message=duplicate_message
    )[0]
    assert duplicate.status == MemoryCandidate.Status.DUPLICATE
    assert duplicate.duplicate_of == old

    changed_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Мой бюджет проекта теперь 300 000 ₽.",
    )
    conflict = extract_memory_candidates(
        user=memory_user, conversation=conversation, source_message=changed_message
    )[0]
    assert conflict.status == MemoryCandidate.Status.CONFLICT
    assert conflict.conflicts_with == old
    assert MemoryItem.objects.filter(owner=memory_user).count() == 1

    accepted = accept_candidate(candidate=conflict, user=memory_user)
    old.refresh_from_db()
    conflict.refresh_from_db()
    assert old.status == MemoryItem.Status.SUPERSEDED
    assert accepted.status == MemoryItem.Status.ACTIVE
    assert accepted.content == "Мой бюджет проекта теперь 300 000 ₽."
    assert accepted.source_kind == "auto_candidate_user"
    assert conflict.status == MemoryCandidate.Status.ACCEPTED


@pytest.mark.django_db
def test_candidate_review_api_is_user_scoped(client, memory_user, settings):
    settings.AUTO_MEMORY_ENABLED = True
    memory_user.preferences.auto_memory_enabled = True
    memory_user.preferences.save(update_fields=["auto_memory_enabled"])
    conversation = Conversation.objects.create(owner=memory_user)
    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Я предпочитаю ответы в виде таблицы.",
    )
    candidate = extract_memory_candidates(
        user=memory_user, conversation=conversation, source_message=message
    )[0]
    assert candidate.status == MemoryCandidate.Status.PENDING
    assert MemoryItem.objects.filter(owner=memory_user).count() == 0
    assert build_memory_context(memory_user, conversation) == ("", [])
    other = User.objects.create_user(
        username="candidate-other", email="candidate-other@example.com", password="password123"
    )
    client.force_login(other)
    assert client.get("/api/v1/memory-candidates/").json() == []
    assert (
        client.post(
            f"/api/v1/memory-candidates/{candidate.id}/accept/",
            {},
            content_type="application/json",
        ).status_code
        == 404
    )

    client.force_login(memory_user)
    response = client.post(
        f"/api/v1/memory-candidates/{candidate.id}/reject/",
        {},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == MemoryCandidate.Status.REJECTED
    assert MemoryItem.objects.filter(owner=memory_user).count() == 0


@pytest.mark.django_db
def test_preferences_cannot_enable_auto_memory_while_platform_flag_is_off(
    client, memory_user, settings
):
    settings.AUTO_MEMORY_ENABLED = False
    client.force_login(memory_user)
    response = client.patch(
        "/api/v1/auth/preferences/",
        {"auto_memory_enabled": True},
        content_type="application/json",
    )
    assert response.status_code == 400
    memory_user.preferences.refresh_from_db()
    assert memory_user.preferences.auto_memory_enabled is False


@pytest.mark.django_db
def test_opt_out_dismisses_pending_candidates(client, memory_user, settings):
    settings.AUTO_MEMORY_ENABLED = True
    memory_user.preferences.auto_memory_enabled = True
    memory_user.preferences.save(update_fields=["auto_memory_enabled"])
    conversation = Conversation.objects.create(owner=memory_user)
    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Я предпочитаю короткие ответы.",
    )
    candidate = extract_memory_candidates(
        user=memory_user, conversation=conversation, source_message=message
    )[0]
    client.force_login(memory_user)
    response = client.patch(
        "/api/v1/auth/preferences/",
        {"auto_memory_enabled": False},
        content_type="application/json",
    )
    assert response.status_code == 200
    candidate.refresh_from_db()
    assert candidate.status == MemoryCandidate.Status.DISMISSED


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
