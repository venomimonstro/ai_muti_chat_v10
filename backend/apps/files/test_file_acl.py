import pytest
from django.core.files.base import ContentFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project, ProjectMembership

from .models import FileAsset


@pytest.mark.django_db(transaction=True)
def test_file_uuid_does_not_bypass_project_acl(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    owner = User.objects.create_user(
        username="file-owner", email="file-owner@example.test", password="password123"
    )
    outsider = User.objects.create_user(
        username="file-outsider", email="file-outsider@example.test", password="password123"
    )
    viewer = User.objects.create_user(
        username="file-viewer", email="file-viewer@example.test", password="password123"
    )
    project = Project.objects.create(owner=owner, name="Private files")
    ProjectMembership.objects.create(
        project=project,
        user=viewer,
        role=ProjectMembership.Role.VIEWER,
    )
    asset = FileAsset(
        owner=owner,
        project=project,
        original_name="private.txt",
        declared_content_type="text/plain",
        detected_type="text",
        size_bytes=6,
        sha256="a" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="acl:file",
    )
    asset.blob.save("private.txt", ContentFile(b"secret"), save=True)

    client = APIClient()
    client.force_authenticate(outsider)
    assert client.get(f"/api/v1/files/{asset.id}/").status_code == 404
    assert client.get(f"/api/v1/files/{asset.id}/download/").status_code == 404
    assert client.get(f"/api/v1/files/{asset.id}/chunks/").status_code == 404
    assert client.delete(f"/api/v1/files/{asset.id}/").status_code == 404

    client.force_authenticate(viewer)
    assert client.get(f"/api/v1/files/{asset.id}/").status_code == 200
    download = client.get(f"/api/v1/files/{asset.id}/download/")
    assert download.status_code == 200
    assert download["Content-Disposition"].startswith("attachment;")
    assert client.delete(f"/api/v1/files/{asset.id}/").status_code == 404

    client.force_authenticate(owner)
    assert client.delete(f"/api/v1/files/{asset.id}/").status_code == 204
