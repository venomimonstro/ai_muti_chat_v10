from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.ai_registry.models import Provider

from .models import ImageModel
from .services import preview, provider_unit_price


@pytest.mark.django_db
def test_single_image_variant_may_use_legacy_scalar_cost():
    provider = Provider.objects.create(slug="image-single", name="Image Single")
    model = ImageModel.objects.create(
        provider=provider,
        slug="image-single-v1",
        display_name="Single",
        upstream_model="single-v1",
        supported_sizes=["1024x1024"],
        supported_qualities=["standard"],
        provider_price_per_image=Decimal("2.00"),
    )

    assert provider_unit_price(model, "1024x1024", "standard") == Decimal("2.00")


@pytest.mark.django_db
def test_multiple_image_variants_require_explicit_price_for_every_combination():
    provider = Provider.objects.create(slug="image-matrix", name="Image Matrix")
    model = ImageModel.objects.create(
        provider=provider,
        slug="image-matrix-v1",
        display_name="Matrix",
        upstream_model="matrix-v1",
        supported_sizes=["1024x1024", "1536x1024"],
        supported_qualities=["standard", "high"],
        provider_price_per_image=Decimal("1.00"),
        provider_price_matrix={
            "1024x1024|standard": "1.00",
            "1024x1024|high": "2.00",
            "1536x1024|standard": "1.50",
        },
    )

    with pytest.raises(ValidationError, match="Не настроена себестоимость"):
        provider_unit_price(model, "1536x1024", "high")


@pytest.mark.django_db
def test_preview_uses_exact_size_and_quality_cost(settings):
    settings.IMAGES_ENABLED = True
    provider = Provider.objects.create(slug="image-priced", name="Image Priced")
    model = ImageModel.objects.create(
        provider=provider,
        slug="image-priced-v1",
        display_name="Priced",
        upstream_model="priced-v1",
        supported_sizes=["1024x1024"],
        supported_qualities=["standard", "high"],
        provider_price_per_image=Decimal("1.00"),
        provider_price_matrix={
            "1024x1024|standard": "1.00",
            "1024x1024|high": "3.00",
        },
        markup_percent=Decimal("100"),
    )

    _model, standard, _prompt, _count = preview(
        model_slug=model.slug,
        prompt="test",
        size="1024x1024",
        quality="standard",
        count=1,
    )
    _model, high, _prompt, _count = preview(
        model_slug=model.slug,
        prompt="test",
        size="1024x1024",
        quality="high",
        count=1,
    )

    assert high.user_charge_rub > standard.user_charge_rub
