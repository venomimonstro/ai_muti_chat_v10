from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.projects.models import Project

from .models import FileAsset


@pytest.fixture
def file_client(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(
        username="file-quota", email="file-quota@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Quota project")
    client = APIClient()
    client.force_authenticate(user)
    return user, project, client


def _text(name, content=b"hello world"):
    return SimpleUploadedFile(name, content, content_type="text/plain")


@pytest.mark.django_db
def test_user_file_count_quota_blocks_second_upload(monkeypatch, file_client):
    _user, project, client = file_client
    monkeypatch.setenv("FILE_USER_MAX_FILES", "1")
    monkeypatch.setenv("FILE_PROJECT_MAX_FILES", "10")
    monkeypatch.setenv("FILE_PROCESSING_ASYNC", "false")

    first = client.post(
        "/api/v1/files/",
        {"project": str(project.id), "file": _text("one.txt")},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="quota:one",
    )
    second = client.post(
        "/api/v1/files/",
        {"project": str(project.id), "file": _text("two.txt")},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="quota:two",
    )

    assert first.status_code == 201
    assert second.status_code == 400
    assert "количества файлов аккаунта" in str(second.data)


@pytest.mark.django_db
def test_deleted_file_releases_storage_quota_but_not_upload_velocity(monkeypatch, file_client):
    _user, project, client = file_client
    monkeypatch.setenv("FILE_USER_MAX_FILES", "1")
    monkeypatch.setenv("FILE_PROJECT_MAX_FILES", "1")
    monkeypatch.setenv("FILE_UPLOADS_PER_MINUTE", "2")
    monkeypatch.setenv("FILE_PROCESSING_ASYNC", "false")
    first = client.post(
        "/api/v1/files/",
        {"project": str(project.id), "file": _text("one.txt")},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="quota:delete:one",
    )
    assert first.status_code == 201
    assert client.delete(f"/api/v1/files/{first.data['id']}/").status_code == 204

    second = client.post(
        "/api/v1/files/",
        {"project": str(project.id), "file": _text("two.txt")},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="quota:delete:two",
    )
    third = client.post(
        "/api/v1/files/",
        {"project": str(project.id), "file": _text("three.txt")},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="quota:delete:three",
    )

    assert second.status_code == 201
    assert third.status_code == 400
    assert "Слишком много загрузок" in str(third.data)


@pytest.mark.django_db
def test_active_processing_limit_blocks_worker_flood(monkeypatch, file_client):
    _user, project, client = file_client
    monkeypatch.setenv("FILE_PROCESSING_ASYNC", "true")
    monkeypatch.setenv("FILE_USER_MAX_ACTIVE_PROCESSING", "1")
    monkeypatch.setenv("FILE_UPLOADS_PER_MINUTE", "20")

    with patch("apps.files.views.process_file_task.delay", return_value=None):
        first = client.post(
            "/api/v1/files/",
            {"project": str(project.id), "file": _text("pending-one.txt")},
            format="multipart",
            HTTP_IDEMPOTENCY_KEY="active:one",
        )
        second = client.post(
            "/api/v1/files/",
            {"project": str(project.id), "file": _text("pending-two.txt")},
            format="multipart",
            HTTP_IDEMPOTENCY_KEY="active:two",
        )

    assert first.status_code == 201
    assert second.status_code == 400
    assert "уже обрабатывается" in str(second.data)


@pytest.mark.django_db
def test_idempotent_file_retry_is_not_blocked_by_velocity_limit(monkeypatch, file_client):
    _user, project, client = file_client
    monkeypatch.setenv("FILE_UPLOADS_PER_MINUTE", "1")
    monkeypatch.setenv("FILE_PROCESSING_ASYNC", "false")

    first = client.post(
        "/api/v1/files/",
        {"project": str(project.id), "file": _text("retry.txt")},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="velocity:retry",
    )
    repeated = client.post(
        "/api/v1/files/",
        {"project": str(project.id), "file": _text("retry.txt")},
        format="multipart",
        HTTP_IDEMPOTENCY_KEY="velocity:retry",
    )

    assert first.status_code == 201
    assert repeated.status_code == 200
    assert repeated.data["id"] == first.data["id"]


@pytest.mark.django_db
def test_production_async_mode_queues_processing_instead_of_extracting_in_request(
    monkeypatch, file_client
):
    _user, project, client = file_client
    monkeypatch.setenv("FILE_PROCESSING_ASYNC", "true")
    queued = []

    with patch(
        "apps.files.views.process_file_task.delay",
        side_effect=lambda asset_id: queued.append(str(asset_id)),
    ):
        response = client.post(
            "/api/v1/files/",
            {"project": str(project.id), "file": _text("queued.txt")},
            format="multipart",
            HTTP_IDEMPOTENCY_KEY="quota:async",
        )

    assert response.status_code == 201
    asset = FileAsset.objects.get(pk=response.data["id"])
    assert asset.status == FileAsset.Status.UPLOADED
    assert queued == [str(asset.id)]
    assert not asset.jobs.exists()
