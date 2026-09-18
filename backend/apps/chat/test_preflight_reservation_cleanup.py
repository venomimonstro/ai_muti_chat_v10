import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import PriceVersion, RequestCost
from apps.billing.services import credit

from .models import Conversation, Generation
from .streaming import prepare


@pytest.mark.django_db(transaction=True)
def test_preflight_failure_after_reserve_releases_wallet_funds(monkeypatch):
    user = User.objects.create_user(
        username="preflight-cleanup",
        email="preflight-cleanup@example.test",
        password="password123!",
    )
    credit(user, Decimal("10"), "test", "preflight-cleanup")
    provider = Provider.objects.create(slug="preflight-provider", name="Preflight Provider")
    model = AIModel.objects.create(
        provider=provider,
        slug="preflight-model",
        display_name="Preflight Model",
        upstream_model="preflight-model",
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    conversation = Conversation.objects.create(owner=user, selected_model=model.slug)

    def fail_request_cost(*_args, **_kwargs):
        raise RuntimeError("synthetic RequestCost failure")

    monkeypatch.setattr(RequestCost.objects, "create", fail_request_cost)

    with pytest.raises(RuntimeError, match="synthetic RequestCost failure"):
        prepare(
            user=user,
            conversation=conversation,
            content="Проверка очистки резерва",
            client_message_id=uuid.uuid4(),
            idempotency_key="preflight:cleanup",
        )

    user.wallet.refresh_from_db()
    generation = Generation.objects.get(owner=user, idempotency_key="preflight:cleanup")
    assert generation.state == Generation.State.FAILED
    assert generation.error_code == "preflight_failed"
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")
