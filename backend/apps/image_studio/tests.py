from decimal import Decimal

import pytest
from django.core.files.storage import default_storage
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.billing.models import CostAnomaly
from apps.billing.services import credit

from .adapters import (
    EchoImageAdapter,
    ImageProviderError,
    ImageProviderResult,
    ImageResult,
    OpenAIImageAdapter,
)
from .models import ImageGeneration, ImageModel


@pytest.fixture
def image_context(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    provider = Provider.objects.create(slug="image-echo", name="Image Echo")
    model = ImageModel.objects.create(
        provider=provider,
        slug="echo-image-v1",
        display_name="Echo Image",
        upstream_model="echo-image-v1",
        provider_price_per_image=Decimal("0.200000"),
        supported_sizes=["1024x1024"],
        supported_qualities=["standard"],
    )
    user = User.objects.create_user(
        username="image-user", email="image@example.com", password="password123"
    )
    credit(user, Decimal("10"), "test", "images")
    client = APIClient()
    client.force_authenticate(user)
    return user, model, client


@pytest.mark.django_db(transaction=True)
def test_image_preview_generation_history_and_idempotency(image_context):
    user, model, client = image_context
    payload = {
        "model": model.slug,
        "prompt": "Фиолетовый космический корабль",
        "size": "1024x1024",
        "quality": "standard",
        "count": 2,
    }
    preview = client.post("/api/v1/images/preview/", payload, format="json")
    assert preview.status_code == 200
    assert Decimal(preview.data["expected_cost_rub"]) == Decimal("0.8000")

    headers = {"HTTP_IDEMPOTENCY_KEY": "image:web:one"}
    first = client.post("/api/v1/images/generations/", payload, format="json", **headers)
    second = client.post("/api/v1/images/generations/", payload, format="json", **headers)
    conflict = client.post(
        "/api/v1/images/generations/",
        {**payload, "prompt": "Другой запрос"},
        format="json",
        **headers,
    )
    assert first.status_code == 201
    assert second.data["id"] == first.data["id"]
    assert conflict.status_code == 400
    assert first.data["actual_count"] == 2
    assert Decimal(first.data["actual_cost_rub"]) == Decimal("0.8000")
    assert ImageGeneration.objects.filter(owner=user).count() == 1
    assert len(first.data["images"]) == 2
    assert default_storage.exists(ImageGeneration.objects.get().images.first().file.name)
    assert len(client.get("/api/v1/images/generations/").data) == 1


@pytest.mark.django_db(transaction=True)
def test_image_source_is_private(image_context):
    _user, model, client = image_context
    result = client.post(
        "/api/v1/images/generations/",
        {
            "model": model.slug,
            "prompt": "Private",
            "size": "1024x1024",
            "quality": "standard",
            "count": 1,
        },
        format="json",
        HTTP_IDEMPOTENCY_KEY="image:private",
    )
    source_url = result.data["images"][0]["source_url"]
    assert client.get(source_url).status_code == 200
    outsider = User.objects.create_user(
        username="image-other", email="other-image@example.com", password="password123"
    )
    client.force_authenticate(outsider)
    assert client.get(source_url).status_code == 404


class FailingImageAdapter:
    def generate(self, **_kwargs):
        raise ImageProviderError("down", code="upstream_down")


class OverDeliveringImageAdapter:
    def generate(self, **_kwargs):
        image = ImageResult(EchoImageAdapter._PNG, "image/png", "unexpected")
        return ImageProviderResult(images=[image, image], provider_request_id="over-delivery")


class UnderDeliveringImageAdapter:
    def generate(self, **_kwargs):
        image = ImageResult(EchoImageAdapter._PNG, "image/png", "partial")
        return ImageProviderResult(images=[image], provider_request_id="under-delivery")


@pytest.mark.django_db(transaction=True)
def test_provider_failure_releases_full_image_reservation(image_context):
    user, model, _client = image_context
    from .services import generate

    generation = generate(
        user=user,
        model_slug=model.slug,
        prompt="Failure",
        size="1024x1024",
        quality="standard",
        count=1,
        idempotency_key="image:failure",
        adapter=FailingImageAdapter(),
    )
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.error_code == "upstream_down"
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_image_provider_over_delivery_is_fail_closed_and_disables_provider(image_context):
    user, model, _client = image_context
    from .services import generate

    generation = generate(
        user=user,
        model_slug=model.slug,
        prompt="One image only",
        size="1024x1024",
        quality="standard",
        count=1,
        idempotency_key="image:over-delivery",
        adapter=OverDeliveringImageAdapter(),
    )

    generation.refresh_from_db()
    model.provider.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.error_code == "image_count_mismatch"
    assert generation.images.count() == 0
    assert model.provider.emergency_disabled is True
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert CostAnomaly.objects.filter(
        severity="critical",
        provider_slug=model.provider.slug,
        details__reason="provider_returned_more_images_than_requested",
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_image_provider_partial_delivery_is_not_charged_as_success(image_context):
    user, model, _client = image_context
    from .services import generate

    generation = generate(
        user=user,
        model_slug=model.slug,
        prompt="Two images expected",
        size="1024x1024",
        quality="standard",
        count=2,
        idempotency_key="image:under-delivery",
        adapter=UnderDeliveringImageAdapter(),
    )

    generation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.error_code == "image_count_mismatch"
    assert generation.images.count() == 0
    assert generation.actual_cost_rub is None
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_image_charge_above_reserved_amount_never_overdraws_customer_and_trips_provider(
    image_context, monkeypatch
):
    user, model, _client = image_context
    from .services import generate

    monkeypatch.setattr(
        "apps.image_studio.services.calculate_flat_from_snapshot",
        lambda *_args, **_kwargs: (
            Decimal("100.0000"),
            Decimal("100.0000"),
            Decimal("0.0000"),
            Decimal("0.0000"),
        ),
    )

    generation = generate(
        user=user,
        model_slug=model.slug,
        prompt="Cost contract mismatch",
        size="1024x1024",
        quality="standard",
        count=1,
        idempotency_key="image:cost-over-reserve",
        adapter=EchoImageAdapter(),
    )

    generation.refresh_from_db()
    model.provider.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.images.count() == 0
    assert model.provider.emergency_disabled is True
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert CostAnomaly.objects.filter(
        severity="critical",
        provider_slug=model.provider.slug,
        details__reason="image_charge_exceeded_reserved_maximum",
    ).exists()


def test_openai_adapter_uses_current_response_format_contract(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            encoded = __import__("base64").b64encode(EchoImageAdapter._PNG).decode()
            return {"data": [{"b64_json": encoded}]}

    def fake_post(*_args, **kwargs):
        calls.append(kwargs["json"])
        return Response()

    monkeypatch.setattr("apps.image_studio.adapters.httpx.post", fake_post)
    adapter = OpenAIImageAdapter(api_key="test", base_url="https://api.openai.com/v1")
    adapter.generate(
        model="gpt-image-1", prompt="test", size="1024x1024", quality="high", count=1
    )
    adapter.generate(
        model="dall-e-3", prompt="test", size="1024x1024", quality="standard", count=1
    )
    assert "response_format" not in calls[0]
    assert calls[1]["response_format"] == "b64_json"
