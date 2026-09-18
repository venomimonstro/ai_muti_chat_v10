from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.b2b_api.models import APIKey, APIUsage, Organization
from apps.chat.models import CompareRun, CompareVariant, Conversation
from apps.image_studio.models import ImageGeneration, ImageModel

from .models import CostAnomaly


@pytest.fixture
def provider_and_text_model():
    provider = Provider.objects.create(slug="loss-watchdog-provider", name="Loss watchdog")
    model = AIModel.objects.create(
        provider=provider,
        slug="loss-watchdog-model",
        display_name="Loss watchdog model",
        upstream_model="loss-v1",
        capabilities=["text"],
    )
    return provider, model


@pytest.mark.django_db
def test_b2b_negative_margin_auto_disables_provider(provider_and_text_model):
    provider, model = provider_and_text_model
    user = User.objects.create_user(
        username="watchdog-b2b", email="watchdog-b2b@example.test", password="password123"
    )
    organization = Organization.objects.create(name="Watchdog", slug="watchdog-b2b", billing_user=user)
    key = APIKey.objects.create(
        organization=organization,
        created_by=user,
        name="test",
        prefix="ak_watchdog_b2b",
        secret_hash="a" * 64,
        scopes=["chat.completions"],
        monthly_limit_rub=Decimal("100"),
    )
    usage = APIUsage.objects.create(
        organization=organization,
        api_key=key,
        model=model,
        response_id="chatcmpl-watchdog",
        request_hash="b" * 64,
        state=APIUsage.State.COMPLETED,
        estimated_cost_rub=Decimal("5"),
        provider_cost_rub=Decimal("8"),
        charged_rub=Decimal("5"),
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is True
    assert CostAnomaly.objects.filter(
        dedupe_key=f"loss-watchdog:b2b_api:{usage.id}", severity="critical"
    ).exists()


@pytest.mark.django_db
def test_compare_negative_margin_auto_disables_provider(provider_and_text_model):
    provider, model = provider_and_text_model
    user = User.objects.create_user(
        username="watchdog-compare",
        email="watchdog-compare@example.test",
        password="password123",
    )
    conversation = Conversation.objects.create(owner=user, title="Compare loss")
    run = CompareRun.objects.create(
        owner=user,
        conversation=conversation,
        prompt="test",
        idempotency_key="compare-loss-watchdog",
        state=CompareRun.State.COMPLETED,
        model_slugs=[model.slug, "other"],
        expected_min_rub=Decimal("1"),
        expected_max_rub=Decimal("10"),
    )
    variant = CompareVariant.objects.create(
        compare_run=run,
        model=model,
        position=0,
        state=CompareVariant.State.COMPLETED,
        expected_min_rub=Decimal("1"),
        expected_max_rub=Decimal("5"),
        actual_cost_rub=Decimal("3"),
        provider_cost_rub=Decimal("7"),
        pricing_snapshot={},
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is True
    assert CostAnomaly.objects.filter(
        dedupe_key=f"loss-watchdog:compare:{variant.id}", severity="critical"
    ).exists()


@pytest.mark.django_db
def test_image_negative_margin_auto_disables_provider():
    provider = Provider.objects.create(slug="loss-image-provider", name="Loss image")
    image_model = ImageModel.objects.create(
        provider=provider,
        slug="loss-image-model",
        display_name="Loss image model",
        upstream_model="image-v1",
        provider_price_per_image=Decimal("5"),
    )
    user = User.objects.create_user(
        username="watchdog-image", email="watchdog-image@example.test", password="password123"
    )
    generation = ImageGeneration.objects.create(
        owner=user,
        model=image_model,
        prompt="test",
        size="1024x1024",
        quality="standard",
        requested_count=1,
        actual_count=1,
        state=ImageGeneration.State.COMPLETED,
        idempotency_key="image-loss-watchdog",
        price_snapshot={},
        estimated_cost_rub=Decimal("4"),
        provider_cost_rub=Decimal("6"),
        actual_cost_rub=Decimal("4"),
    )

    provider.refresh_from_db()
    assert provider.emergency_disabled is True
    assert CostAnomaly.objects.filter(
        dedupe_key=f"loss-watchdog:images:{generation.id}", severity="critical"
    ).exists()
