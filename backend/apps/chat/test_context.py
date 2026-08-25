import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderStreamEvent
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion
from apps.billing.services import credit
from apps.files.models import FileAsset, FileChunk
from apps.files.rag import prepare_chunk
from apps.memory_store.models import MemoryItem
from apps.projects.models import Project, ProjectInstruction

from .context import assemble_context, refresh_rolling_summary
from .models import Conversation, ConversationSummary, Message
from .streaming import prepare, run


def registry(*, context_window=4096):
    provider = Provider.objects.create(slug=f"echo-{context_window}", name="Echo")
    model = AIModel.objects.create(
        provider=provider,
        slug=f"echo-{context_window}",
        display_name="Echo",
        upstream_model="echo",
        context_window=context_window,
        max_output_tokens=1024,
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    return model


def add_pair(conversation, user_text, assistant_text="Ответ"):
    Message.objects.create(conversation=conversation, role=Message.Role.USER, content=user_text)
    return Message.objects.create(
        conversation=conversation, role=Message.Role.ASSISTANT, content=assistant_text
    )


@pytest.mark.django_db
def test_context_is_strictly_bounded_and_keeps_current_message(settings):
    settings.SMART_CONTEXT_RECENT_TURNS = 2
    user = User.objects.create_user(username="bounded", password="password123")
    model = registry(context_window=700)
    conversation = Conversation.objects.create(owner=user, selected_model=model.slug)
    add_pair(conversation, "старая история " * 100)
    current = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="текущий запрос " * 100
    )
    assistant = Message.objects.create(conversation=conversation, role=Message.Role.ASSISTANT)

    snapshot, _ = assemble_context(
        user=user,
        conversation=conversation,
        assistant_message=assistant,
        model=model,
        output_tokens=1024,
    )

    assert snapshot["budget"]["input_tokens"] <= snapshot["budget"]["input_limit"]
    assert snapshot["budget"]["input_limit"] + snapshot["budget"]["output_reserved"] + 32 <= 700
    current_component = next(
        item for item in snapshot["components"] if item["source_id"] == str(current.id)
    )
    assert current_component["truncated"] is True
    assert snapshot["provider_messages"][-1]["role"] == "user"


@pytest.mark.django_db
def test_retrieval_is_relevant_deduplicated_and_project_isolated(settings):
    settings.SMART_CONTEXT_RECENT_TURNS = 1
    user = User.objects.create_user(username="retrieval", password="password123")
    model = registry()
    project = Project.objects.create(owner=user, name="Alpha")
    other_project = Project.objects.create(owner=user, name="Beta")
    ProjectInstruction.objects.create(
        project=project, content="Отвечай кратко", version=1, created_by=user
    )
    conversation = Conversation.objects.create(
        owner=user, selected_model=model.slug, project=project
    )
    duplicate = "бюджет проекта составляет три миллиона"
    MemoryItem.objects.create(
        owner=user,
        scope=MemoryItem.Scope.PROJECT,
        project=project,
        content=duplicate,
        normalized_content=duplicate,
    )
    own_file = FileAsset.objects.create(
        owner=user,
        project=project,
        blob="test/own.txt",
        original_name="budget.txt",
        detected_type="txt",
        size_bytes=10,
        sha256="a" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="own",
    )
    other_file = FileAsset.objects.create(
        owner=user,
        project=other_project,
        blob="test/other.txt",
        original_name="secret.txt",
        detected_type="txt",
        size_bytes=10,
        sha256="b" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="other",
    )
    prepare_chunk(FileChunk(position=0, content=duplicate), own_file).save()
    prepare_chunk(
        FileChunk(position=0, content="бюджет проекта секретный чужой документ"), other_file
    ).save()
    add_pair(conversation, "промежуточный разговор")
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Что известно про бюджет проекта?",
    )
    assistant = Message.objects.create(conversation=conversation, role=Message.Role.ASSISTANT)

    snapshot, selected = assemble_context(
        user=user,
        conversation=conversation,
        assistant_message=assistant,
        model=model,
        output_tokens=512,
    )
    kinds = [item["kind"] for item in snapshot["components"]]
    contents = "\n".join(item["content"] for item in snapshot["components"])

    assert "project_instruction" in kinds
    assert "memory" in kinds
    assert len(selected) == 1
    assert "secret.txt" not in contents
    assert kinds.count("file_chunk") == 0  # exact fact already supplied by memory
    assert snapshot["dropped_or_deduplicated"] >= 1


@pytest.mark.django_db
def test_rolling_summary_advances_without_replacing_source_history(settings):
    settings.SMART_CONTEXT_RECENT_TURNS = 1
    settings.SMART_CONTEXT_SUMMARY_CHARS = 200
    user = User.objects.create_user(username="summary", password="password123")
    conversation = Conversation.objects.create(owner=user)
    for number in range(3):
        add_pair(conversation, f"Вопрос {number}", f"Ответ {number}")

    summary = refresh_rolling_summary(conversation)
    first_version = summary.version
    assert summary.source_message_count == 4
    assert conversation.messages.count() == 6
    assert refresh_rolling_summary(conversation).version == first_version

    add_pair(conversation, "Новый вопрос", "Новый ответ")
    summary = refresh_rolling_summary(conversation)
    assert summary.version == first_version + 1
    assert summary.source_message_count == 6
    assert conversation.messages.count() == 8


@pytest.mark.django_db
def test_file_context_has_untrusted_boundary_and_citation(settings):
    user = User.objects.create_user(username="citation", password="password123")
    model = registry()
    project = Project.objects.create(owner=user, name="Sources")
    conversation = Conversation.objects.create(
        owner=user, selected_model=model.slug, project=project
    )
    asset = FileAsset.objects.create(
        owner=user,
        project=project,
        blob="test/source.txt",
        original_name="source.txt",
        detected_type="txt",
        size_bytes=10,
        sha256="c" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="citation",
    )
    chunk = prepare_chunk(
        FileChunk(
            position=0, source_location={"source": "document"}, content="Срок сдачи — декабрь"
        ),
        asset,
    )
    chunk.save()
    Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="Какой срок сдачи?"
    )
    assistant = Message.objects.create(conversation=conversation, role=Message.Role.ASSISTANT)

    snapshot, _ = assemble_context(
        user=user,
        conversation=conversation,
        assistant_message=assistant,
        model=model,
        output_tokens=512,
    )

    assert len(snapshot["citations"]) == 1
    citation = snapshot["citations"][0]
    assert citation["file_name"] == "source.txt"
    assert citation["content_sha256"] == chunk.content_sha256
    component = next(item for item in snapshot["components"] if item["kind"] == "file_chunk")
    assert component["citation"]["id"] == citation["id"]
    assert "FILE_DATA" in component["content"]
    assert citation["id"] in component["content"]


class CaptureAdapter:
    def __init__(self):
        self.messages = None

    def stream(self, *, messages, **_kwargs):
        self.messages = messages
        yield ProviderStreamEvent(kind="delta", text_delta="Готово")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id="capture:1",
            input_tokens=10,
            output_tokens=2,
        )


@pytest.mark.django_db(transaction=True)
def test_stream_uses_immutable_context_snapshot(settings):
    settings.SMART_CONTEXT_RECENT_TURNS = 1
    user = User.objects.create_user(username="snapshot", password="password123")
    model = registry()
    credit(user, 10, "test", "snapshot")
    conversation = Conversation.objects.create(owner=user, selected_model=model.slug)
    old = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="Оригинальный контекст"
    )
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Продолжай",
        client_message_id=uuid.uuid4(),
        idempotency_key="snapshot:1",
    )
    original_hash = generation.context_snapshot["sha256"]
    old.content = "Контекст изменён после preflight"
    old.save(update_fields=["content"])
    adapter = CaptureAdapter()

    list(run(generation, adapter=adapter))

    assert created is True
    assert original_hash == generation.context_snapshot["sha256"]
    sent = "\n".join(item["content"] for item in adapter.messages)
    assert "Оригинальный контекст" in sent
    assert "изменён после preflight" not in sent
    assert ConversationSummary.objects.filter(conversation=conversation).exists()
