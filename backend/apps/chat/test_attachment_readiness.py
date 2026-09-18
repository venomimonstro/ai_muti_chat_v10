import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from apps.accounts.models import User
from apps.files.models import FileAsset
from apps.projects.models import Project

from .attachments import resolve_chat_attachments
from .models import Conversation


def _asset(*, user, project, name, status, error_code=""):
    item = FileAsset(
        owner=user,
        project=project,
        original_name=name,
        declared_content_type="application/pdf",
        detected_type="pdf",
        size_bytes=5,
        sha256=("a" if name.startswith("scan") else "b") * 64,
        status=status,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        error_code=error_code,
        idempotency_key=f"attachment:{name}",
    )
    item.blob.save(name, ContentFile(b"hello"), save=True)
    return item


@pytest.mark.django_db(transaction=True)
def test_scanned_pdf_without_text_layer_is_not_sent_to_ai(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(
        username="scan-file", email="scan-file@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Files")
    conversation = Conversation.objects.create(owner=user, project=project)
    asset = _asset(
        user=user,
        project=project,
        name="scan.pdf",
        status=FileAsset.Status.PARTIAL,
        error_code="pdf_text_layer_missing",
    )

    with pytest.raises(ValidationError, match="скан без текстового слоя"):
        resolve_chat_attachments(
            user=user,
            conversation=conversation,
            file_ids=[str(asset.id)],
        )


@pytest.mark.django_db(transaction=True)
def test_file_still_processing_is_not_sent_to_ai(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(
        username="processing-file",
        email="processing-file@example.test",
        password="password123",
    )
    project = Project.objects.create(owner=user, name="Files")
    conversation = Conversation.objects.create(owner=user, project=project)
    asset = _asset(
        user=user,
        project=project,
        name="processing.pdf",
        status=FileAsset.Status.PARSING,
    )

    with pytest.raises(ValidationError, match="ещё обрабатывается"):
        resolve_chat_attachments(
            user=user,
            conversation=conversation,
            file_ids=[str(asset.id)],
        )
