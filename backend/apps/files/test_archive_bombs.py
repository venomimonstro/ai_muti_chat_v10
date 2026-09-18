import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project, ProjectMembership


def _bomb_docx():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            b"<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>",
        )
        archive.writestr(
            "word/document.xml",
            b"<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            + b"A" * 200_000
            + b"</w:document>",
        )
    return payload.getvalue()


@pytest.mark.django_db
def test_docx_with_suspicious_compression_ratio_is_rejected(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.FILE_MAX_COMPRESSION_RATIO = 10
    settings.FILE_MAX_UPLOAD_BYTES = 2 * 1024 * 1024
    settings.FILE_MAX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024
    user = User.objects.create_user(
        username="zip-bomb", email="zip-bomb@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Bomb guard")
    ProjectMembership.objects.create(
        project=project, user=user, role=ProjectMembership.Role.OWNER
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.post(
        "/api/v1/files/",
        {
            "project": str(project.id),
            "file": SimpleUploadedFile(
                "bomb.docx",
                _bomb_docx(),
                content_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
            ),
        },
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="zip-bomb:1",
    )

    assert response.status_code == 400
    assert "сжат" in str(response.data).lower()
