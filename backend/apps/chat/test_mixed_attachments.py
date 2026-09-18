from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion
from apps.billing.services import credit
from apps.files.models import FileAsset, FileChunk
from apps.projects.models import Project

from .models import Conversation, Generation


@pytest.mark.django_db(transaction=True)
def test_pdf_attachment_uses_bounded_file_context_without_requiring_vision_model():
    user = User.objects.create_user(
        username="document-attachment",
        email="document-attachment@example.test",
        password="password123",
    )
    credit(user, Decimal("50"), "test", "document-attachment")
    provider = Provider.objects.create(
        slug="document-echo",
        name="Document Echo",
        adapter_type=Provider.AdapterType.ECHO,
    )
    model = AIModel.objects.create(
        provider=provider,
        slug="document-echo-v1",
        display_name="Document Echo",
        upstream_model="document-echo-v1",
        capabilities=["text", "streaming"],
        context_window=8192,
        max_output_tokens=2048,
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    project = Project.objects.create(owner=user, name="Документы")
    conversation = Conversation.objects.create(
        owner=user,
        project=project,
        selected_model=model.slug,
        routing_mode=Conversation.RoutingMode.MANUAL,
    )
    asset = FileAsset.objects.create(
        owner=user,
        project=project,
        blob="users/test/report.pdf",
        original_name="report.pdf",
        declared_content_type="application/pdf",
        detected_type="pdf",
        size_bytes=1024,
        sha256="a" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="document-attachment:file",
    )
    chunk = FileChunk.objects.create(
        file=asset,
        position=0,
        content="Выручка проекта за квартал составила 12 миллионов рублей.",
        content_sha256="b" * 64,
        acl_owner_id=user.id,
        acl_project_id=project.id,
        injection_risk=FileChunk.InjectionRisk.SAFE,
    )

    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream/",
        {
            "content": "Какая выручка указана в прикреплённом отчёте?",
            "client_message_id": "cd3d3cf9-8c88-4e8d-ac6f-d303f506b0b3",
            "file_ids": [str(asset.id)],
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="document-attachment:message",
    )

    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode("utf-8")
    assert "event: completed" in body
    generation = Generation.objects.get(owner=user)
    snapshot = generation.context_snapshot
    assert snapshot["attached_files"][0]["file_id"] == str(asset.id)
    assert snapshot["vision_assets"] == []
    assert snapshot["attached_document_context"]["used"] is True
    assert snapshot["attached_document_context"]["file_ids"] == [str(asset.id)]
    assert any(
        component.get("kind") == "attached_file_chunk"
        and component.get("source_id") == str(chunk.id)
        for component in snapshot["components"]
    )


@pytest.mark.django_db
def test_attachment_cannot_cross_project_or_owner_boundary():
    owner = User.objects.create_user(
        username="attachment-owner", email="attachment-owner@example.test", password="password123"
    )
    outsider = User.objects.create_user(
        username="attachment-outsider", email="attachment-outsider@example.test", password="password123"
    )
    project = Project.objects.create(owner=owner, name="Owner project")
    other_project = Project.objects.create(owner=owner, name="Other project")
    conversation = Conversation.objects.create(owner=owner, project=project)
    wrong_project_file = FileAsset.objects.create(
        owner=owner,
        project=other_project,
        blob="users/test/wrong.pdf",
        original_name="wrong.pdf",
        declared_content_type="application/pdf",
        detected_type="pdf",
        size_bytes=100,
        sha256="c" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="wrong-project",
    )
    foreign_project = Project.objects.create(owner=outsider, name="Foreign")
    foreign_file = FileAsset.objects.create(
        owner=outsider,
        project=foreign_project,
        blob="users/test/foreign.pdf",
        original_name="foreign.pdf",
        declared_content_type="application/pdf",
        detected_type="pdf",
        size_bytes=100,
        sha256="d" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="foreign-file",
    )

    from .attachments import resolve_chat_attachments

    with pytest.raises(Exception, match="недоступны"):
        resolve_chat_attachments(
            user=owner,
            conversation=conversation,
            file_ids=[wrong_project_file.id],
        )
    with pytest.raises(Exception, match="недоступны"):
        resolve_chat_attachments(
            user=owner,
            conversation=conversation,
            file_ids=[foreign_file.id],
        )
