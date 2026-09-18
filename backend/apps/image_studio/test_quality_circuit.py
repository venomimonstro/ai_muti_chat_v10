from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.billing.models import CostAnomaly

from .adapters import ImageProviderError, ImageProviderResult, ImageResult
from .models import ImageModel
from .services import generate


class InvalidImageAdapter:
    def generate(self, **_kwargs):
        return ImageProviderResult(
            images=[ImageResult(b"not-an-image", "image/png")],
            provider_request_id="broken-image",
        )


@pytest.mark.django_db(transaction=True)
def test_repeated_invalid_results_disable_only_image_model(settings):
    settings.IMAGES_ENABLED = True
    provider = Provider.objects.create(slug="quality-provider", name="Quality provider")
    model = ImageModel.objects.create(
        provider=provider,
        slug="quality-image-model",
        display_name="Quality image",
        upstream_model="quality-v1",
        provider_price_per_image=Decimal("1"),
        supported_sizes=["1024x1024"],
        supported_qualities=["standard"],
    )
    user = User.objects.create_user(
        username="quality-user", email="quality@example.test", password="password123"
    )
    from apps.billing.services import credit

    credit(user, Decimal("100"), "test", "quality-balance")

    for index in range(3):
        result = generate(
            user=user,
            model_slug=model.slug,
            prompt=f"bad {index}",
            size="1024x1024",
            quality="standard",
            count=1,
            idempotency_key=f"quality:{index}",
            adapter=InvalidImageAdapter(),
        )
        assert result.state == result.State.FAILED

    model.refresh_from_db()
    provider.refresh_from_db()
    user.wallet.refresh_from_db()
    assert model.enabled is False
    assert provider.emergency_disabled is False
    assert user.wallet.available_rub == Decimal("100.0000")
    assert CostAnomaly.objects.filter(
        dedupe_key__startswith="image-quality-circuit:",
        severity="critical",
        model_slug=model.slug,
    ).exists()
