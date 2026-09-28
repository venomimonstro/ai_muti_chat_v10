import hashlib
from decimal import Decimal

import pytest
from django.core.files.base import ContentFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.billing.services import credit
from apps.files.models import FileAsset
from apps.projects.models import Project

from .adapters import (
    EchoImageAdapter,
    ImageProviderError,
    ImageProviderResult,
    ImageResult,
    OpenAIImageAdapter,
    adapter_for,
)
from .models import ImageGeneration, ImageModel
from .services import edit, execute_generation, prepare_generation


@pytest.fixture
def edit_context(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.IMAGE_CONFIRM_THRESHOLD_RUB = Decimal("999")
    provider = Provider.objects.create(slug="image-edit-echo", name="Image Edit Echo")
    model = ImageModel.objects.create(
        provider=provider,
        slug="echo-image-edit-v1",
        display_name="Echo Image Edit",
        upstream_model="gpt-image-1",
        provider_price_per_image=Decimal("0.200000"),
        supported_sizes=["1024x1024"],
        supported_qualities=["standard"],
    )
    user = User.objects.create_user(username="edit-user", email="edit@example.com", password="password123")
    outsider = User.objects.create_user(username="edit-other", email="edit-other@example.com", password="password123")
    credit(user, Decimal("10"), "test", "image-edit")
    project = Project.objects.create(owner=user, name="Edit project")
    asset = FileAsset(
        owner=user,
        project=project,
        original_name="source.png",
        declared_content_type="image/png",
        detected_type="png",
        size_bytes=len(EchoImageAdapter._PNG),
        sha256=hashlib.sha256(EchoImageAdapter._PNG).hexdigest(),
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="source:image:1",
    )
    asset.blob.save("source.png", ContentFile(EchoImageAdapter._PNG), save=True)
    client = APIClient()
    client.force_authenticate(user)
    return user, outsider, model, project, asset, client


class SuccessfulEditAdapter:
    def edit(self, **kwargs):
        assert kwargs["media_type"] == "image/png"
        assert kwargs["image"] == EchoImageAdapter._PNG
        return ImageProviderResult(
            images=[ImageResult(EchoImageAdapter._PNG, "image/png", "edited")],
            provider_request_id="edit:test",
        )

    def generate(self, **_kwargs):
        raise AssertionError("edit path must not call generate")


class FailingEditAdapter(SuccessfulEditAdapter):
    def edit(self, **_kwargs):
        raise ImageProviderError("edit provider down", code="upstream_down")


@pytest.mark.django_db(transaction=True)
def test_edit_uses_owner_scoped_source_and_settles_only_success(edit_context):
    user, _outsider, model, _project, asset, _client = edit_context
    generation = edit(
        user=user,
        source_file=asset,
        model_slug=model.slug,
        prompt="Сделай фон светлым",
        size="1024x1024",
        quality="standard",
        count=1,
        idempotency_key="image:edit:success",
        adapter=SuccessfulEditAdapter(),
    )
    generation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == ImageGeneration.State.COMPLETED
    assert generation.images.count() == 1
    assert generation.price_snapshot["operation"] == "edit"
    assert generation.price_snapshot["source_file_id"] == str(asset.id)
    assert generation.price_snapshot["source_file_sha256"] == asset.sha256
    assert generation.actual_cost_rub == Decimal("0.4000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_failed_edit_releases_full_reservation(edit_context):
    user, _outsider, model, _project, asset, _client = edit_context
    generation = edit(
        user=user,
        source_file=asset,
        model_slug=model.slug,
        prompt="Failure",
        size="1024x1024",
        quality="standard",
        count=1,
        idempotency_key="image:edit:failure",
        adapter=FailingEditAdapter(),
    )
    generation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.error_code == "upstream_down"
    assert generation.images.count() == 0
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_edit_endpoint_rejects_other_users_source(edit_context):
    _user, outsider, model, _project, asset, client = edit_context
    client.force_authenticate(outsider)
    response = client.post(
        "/api/v1/images/generations/edit/",
        {
            "source_file": str(asset.id),
            "model": model.slug,
            "prompt": "Steal source",
            "size": "1024x1024",
            "quality": "standard",
            "count": 1,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="image:edit:idor",
    )
    assert response.status_code == 400
    assert ImageGeneration.objects.filter(owner=outsider).count() == 0


@pytest.mark.django_db(transaction=True)
def test_edit_fails_closed_if_source_metadata_changes_after_reservation(edit_context):
    user, _outsider, model, _project, asset, _client = edit_context
    generation, created = prepare_generation(
        user=user,
        source_file=asset,
        operation="edit",
        model_slug=model.slug,
        prompt="Сделай фон белым",
        size="1024x1024",
        quality="standard",
        count=1,
        idempotency_key="image:edit:source-tamper",
        deferred=True,
    )
    assert created is True
    asset.sha256 = "0" * 64
    asset.save(update_fields=["sha256"])

    execute_generation(generation, adapter=SuccessfulEditAdapter())

    generation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.error_code == "invalid_source_image"
    assert generation.images.count() == 0
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


def test_openai_edit_adapter_uses_multipart_image(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            import base64
            return {"id": "edit-openai", "data": [{"b64_json": base64.b64encode(EchoImageAdapter._PNG).decode()}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("apps.image_studio.adapters.httpx.post", fake_post)
    adapter = OpenAIImageAdapter(api_key="test", base_url="https://api.openai.com/v1")
    result = adapter.edit(
        model="gpt-image-1",
        prompt="replace background",
        size="1024x1024",
        quality="high",
        count=1,
        image=EchoImageAdapter._PNG,
        media_type="image/png",
    )
    assert len(result.images) == 1
    assert calls[0][0].endswith("/images/edits")
    assert calls[0][1]["data"]["model"] == "gpt-image-1"
    assert calls[0][1]["data"]["n"] == "1"
    assert calls[0][1]["files"]["image"][2] == "image/png"


@pytest.mark.django_db
def test_openai_image_adapter_reuses_encrypted_provider_credential(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = Provider.objects.create(
        slug="openai-images-shared-credential",
        name="OpenAI shared credential",
        api_base_url="https://api.openai.com/v1",
        credential_env="OPENAI_API_KEY",
    )
    provider.set_api_key("encrypted-db-openai-key")
    provider.save(update_fields=["credential_secret"])
    model = ImageModel.objects.create(
        provider=provider,
        slug="openai-image-shared-credential-v1",
        display_name="OpenAI Image",
        upstream_model="gpt-image-1",
        adapter_type=ImageModel.AdapterType.OPENAI_IMAGES,
        provider_price_per_image=Decimal("0.200000"),
        supported_sizes=["1024x1024"],
        supported_qualities=["standard"],
    )

    adapter = adapter_for(model)

    assert isinstance(adapter, OpenAIImageAdapter)
    assert adapter.api_key == "encrypted-db-openai-key"
