from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.billing.models import BalanceReservation
from apps.billing.services import credit

from .adapters import EchoImageAdapter, ImageProviderResult, ImageResult
from .models import ImageGeneration, ImageModel
from .services import prepare_generation
from .tasks import execute_image_generation_task


@pytest.fixture
def async_image_context(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.IMAGES_ENABLED = True
    settings.IMAGE_CONFIRM_THRESHOLD_RUB = "1000.00"
    provider = Provider.objects.create(slug="async-image-provider", name="Async image")
    model = ImageModel.objects.create(
        provider=provider,
        slug="async-image-model",
        display_name="Async image model",
        upstream_model="image-v1",
        provider_price_per_image=Decimal("1.000000"),
        markup_percent=Decimal("100"),
        supported_sizes=["1024x1024"],
        supported_qualities=["standard"],
    )
    user = User.objects.create_user(
        username="async-image-user",
        email="async-image@example.test",
        password="password123",
    )
    credit(user, Decimal("100"), "test", "async-images")
    client = APIClient()
    client.force_authenticate(user)
    return user, model, client


@pytest.mark.django_db(transaction=True)
def test_async_image_api_returns_queued_and_reserves_before_provider_call(
    monkeypatch, async_image_context
):
    user, model, client = async_image_context
    monkeypatch.setenv("IMAGE_PROCESSING_ASYNC", "true")
    queued = []
    payload = {
        "model": model.slug,
        "prompt": "Красный автомобиль",
        "size": "1024x1024",
        "quality": "standard",
        "count": 1,
    }

    with patch(
        "apps.image_studio.views.execute_image_generation_task.delay",
        side_effect=lambda generation_id: queued.append(str(generation_id)),
    ):
        first = client.post(
            "/api/v1/images/generations/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="async:image:one",
        )
        second = client.post(
            "/api/v1/images/generations/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="async:image:one",
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.data["id"] == second.data["id"]
    assert first.data["state"] == ImageGeneration.State.QUEUED
    assert queued == [first.data["id"]]
    generation = ImageGeneration.objects.get(pk=first.data["id"])
    assert generation.reservation.state == BalanceReservation.State.ACTIVE
    user.wallet.refresh_from_db()
    assert user.wallet.reserved_rub == generation.estimated_cost_rub
    assert user.wallet.available_rub == Decimal("100.0000") - generation.estimated_cost_rub


@pytest.mark.django_db(transaction=True)
def test_async_queue_failure_marks_generation_failed_and_restores_balance(
    monkeypatch, async_image_context
):
    user, model, client = async_image_context
    monkeypatch.setenv("IMAGE_PROCESSING_ASYNC", "true")
    payload = {
        "model": model.slug,
        "prompt": "Синий автомобиль",
        "size": "1024x1024",
        "quality": "standard",
        "count": 1,
    }

    with patch(
        "apps.image_studio.views.execute_image_generation_task.delay",
        side_effect=RuntimeError("broker down"),
    ):
        response = client.post(
            "/api/v1/images/generations/",
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="async:image:broker-down",
        )

    assert response.status_code == 503
    generation = ImageGeneration.objects.get(owner=user, idempotency_key="async:image:broker-down")
    generation.reservation.refresh_from_db()
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.error_code == "queue_unavailable"
    assert generation.reservation.state == BalanceReservation.State.RELEASED
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


class CountingAdapter:
    def __init__(self):
        self.calls = 0

    def generate(self, **_kwargs):
        self.calls += 1
        return ImageProviderResult(
            images=[ImageResult(EchoImageAdapter._PNG, "image/png", "ok")],
            provider_request_id="provider:once",
        )


@pytest.mark.django_db(transaction=True)
def test_duplicate_celery_delivery_executes_provider_only_once(monkeypatch, async_image_context):
    user, model, _client = async_image_context
    generation, created = prepare_generation(
        user=user,
        model_slug=model.slug,
        prompt="Зелёный автомобиль",
        size="1024x1024",
        quality="standard",
        count=1,
        idempotency_key="async:image:duplicate-task",
        confirmed=True,
        deferred=True,
    )
    assert created is True
    adapter = CountingAdapter()
    monkeypatch.setattr("apps.image_studio.services.adapter_for", lambda _model: adapter)

    first = execute_image_generation_task.run(str(generation.id))
    second = execute_image_generation_task.run(str(generation.id))

    generation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert first["state"] == ImageGeneration.State.COMPLETED
    assert second["state"] == ImageGeneration.State.COMPLETED
    assert adapter.calls == 1
    assert generation.images.count() == 1
    assert generation.reservation.state == BalanceReservation.State.SETTLED
    assert user.wallet.reserved_rub == Decimal("0.0000")
    assert user.wallet.available_rub == Decimal("100.0000") - generation.actual_cost_rub
