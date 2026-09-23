from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider

from .branches import ensure_active_branch
from .models import CompareRun, CompareVariant, Conversation
from .public_compare import public_model_identity, serialize_compare_public


def _internal_model():
    provider = Provider.objects.create(slug="gigachat", name="Internal provider")
    return AIModel.objects.create(
        provider=provider,
        slug="gigachat-2-max",
        display_name="GigaChat 2 Max",
        upstream_model="GigaChat-2-Max",
        capabilities=["text", "streaming"],
    )


@pytest.mark.django_db
def test_historical_compare_run_serializes_internal_model_as_system_level():
    user = User.objects.create_user(username="public-compare-history", email="pch@example.test")
    conversation = Conversation.objects.create(owner=user)
    branch = ensure_active_branch(conversation, user)
    model = _internal_model()
    run = CompareRun.objects.create(
        owner=user,
        conversation=conversation,
        branch=branch,
        prompt="test",
        idempotency_key="historic-internal-compare",
        state=CompareRun.State.COMPLETED,
        model_slugs=[model.slug],
        expected_min_rub=Decimal("0.1000"),
        expected_max_rub=Decimal("0.2000"),
        actual_cost_rub=Decimal("0.1500"),
        synthesis_model_slug=model.slug,
        synthesis_output="summary",
        synthesis_cost_rub=Decimal("0.0100"),
    )
    CompareVariant.objects.create(
        compare_run=run,
        model=model,
        position=0,
        state=CompareVariant.State.COMPLETED,
        output="answer",
        expected_min_rub=Decimal("0.1000"),
        expected_max_rub=Decimal("0.2000"),
        actual_cost_rub=Decimal("0.1500"),
    )

    payload = serialize_compare_public(run)

    assert payload["models"] == ["System Max"]
    assert payload["synthesis_model"] == "System Max"
    assert payload["variants"][0]["model"] == "System Max"
    assert payload["variants"][0]["model_name"] == "System Max"
    assert payload["variants"][0]["provider"] == "system"
    assert "gigachat" not in str(payload).casefold()


@pytest.mark.django_db
def test_provider_slug_masks_even_renamed_internal_compare_model():
    provider = Provider.objects.create(slug="gigachat", name="Internal renamed provider")
    model = AIModel.objects.create(
        provider=provider,
        slug="private-reasoning-v3",
        display_name="Private Reasoning Max",
        upstream_model="private-max",
        capabilities=["text"],
    )

    identity = public_model_identity(model)

    assert identity == {"model": "System Max", "model_name": "System Max", "provider": "system"}
    assert "private-reasoning-v3" not in str(identity)


@pytest.mark.django_db
def test_current_compare_api_rejects_direct_selection_of_internal_model():
    user = User.objects.create_user(username="public-compare-api", email="pca@example.test")
    conversation = Conversation.objects.create(owner=user)
    ensure_active_branch(conversation, user)
    model = _internal_model()
    client = APIClient()
    client.force_authenticate(user)

    current = client.post(
        "/api/v1/compare/preview/",
        {"prompt": "test", "models": [model.slug]},
        format="json",
    )
    legacy = client.post(
        f"/api/v1/conversations/{conversation.id}/compare/preview/",
        {"prompt": "test", "models": [model.slug]},
        format="json",
    )

    assert current.status_code == 400
    assert legacy.status_code == 400
    assert "gigachat" not in str(current.data).casefold()
    assert "gigachat" not in str(legacy.data).casefold()
