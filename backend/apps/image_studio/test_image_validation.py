from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.billing.services import credit

from .adapters import ImageProviderResult, ImageResult
from .models import ImageGeneration, ImageModel
from .services import generate
from .validation import validate_generated_image


def _png(color, size=(128, 128)):
    image = Image.new("RGB", size, color=color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_blank_black_image_is_rejected():
    with pytest.raises(Exception) as exc_info:
        validate_generated_image(_png((0, 0, 0)))
    assert getattr(exc_info.value, "code", None) == "blank_image"


def test_corrupt_image_payload_is_rejected():
    with pytest.raises(Exception) as exc_info:
        validate_generated_image(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    assert getattr(exc_info.value, "code", None) == "invalid_image"


class BlankAdapter:
    def generate(self, **_kwargs):
        return ImageProviderResult(
            images=[ImageResult(_png((0, 0, 0)), "image/png")],
            provider_request_id="blank-provider-result",
        )


@pytest.mark.django_db(transaction=True)
def test_blank_provider_result_releases_full_reservation_and_charges_zero(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    settings.IMAGES_ENABLED = True
    provider = Provider.objects.create(slug="blank-image-provider", name="Blank image provider")
    model = ImageModel.objects.create(
        provider=provider,
        slug="blank-image-model",
        display_name="Blank image model",
        upstream_model="blank-v1",
        provider_price_per_image=Decimal("1.00"),
        markup_percent=Decimal("100"),
        supported_sizes=["1024x1024"],
        supported_qualities=["standard"],
    )
    user = User.objects.create_user(
        username="blank-image-user", email="blank-image@example.test", password="password123"
    )
    credit(user, Decimal("100"), "test", "blank-image")

    generation = generate(
        user=user,
        model_slug=model.slug,
        prompt="Сделай изображение",
        size="1024x1024",
        quality="standard",
        count=1,
        idempotency_key="blank-image:1",
        adapter=BlankAdapter(),
    )

    generation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.error_code == "blank_image"
    assert generation.actual_cost_rub is None
    assert generation.images.count() == 0
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
