import uuid
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import BalanceReservation, PriceVersion, RequestCost
from apps.billing.services import credit

from . import streaming
from .models import Conversation, Generation, Message
from .preflight_terminal import classify_preflight_exception
from .streaming import prepare


def _setup_preflight_case(suffix):
    user = User.objects.create_user(
        username=f"preflight-cleanup-{suffix}",
        email=f"preflight-cleanup-{suffix}@example.test",
        password="password123!",
    )
    credit(user, Decimal("10"), "test", f"preflight-cleanup-{suffix}")
    provider = Provider.objects.create(
        slug=f"preflight-provider-{suffix}", name=f"Preflight Provider {suffix}"
    )
    model = AIModel.objects.create(
        provider=provider,
        slug=f"preflight-model-{suffix}",
        display_name=f"Preflight Model {suffix}",
        upstream_model=f"preflight-model-{suffix}",
    )
    PriceVersion.objects.create(
        model_slug=model.slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    conversation = Conversation.objects.create(owner=user, selected_model=model.slug)
    return user, conversation


@pytest.mark.django_db(transaction=True)
def test_preflight_failure_after_reserve_releases_wallet_funds(monkeypatch):
    user, conversation = _setup_preflight_case("normal")

    def fail_request_cost(*_args, **_kwargs):
        raise RuntimeError("synthetic RequestCost failure")

    monkeypatch.setattr(RequestCost.objects, "create", fail_request_cost)

    with pytest.raises(RuntimeError, match="synthetic RequestCost failure"):
        prepare(
            user=user,
            conversation=conversation,
            content="Проверка очистки резерва",
            client_message_id=uuid.uuid4(),
            idempotency_key="preflight:cleanup:normal",
        )

    user.wallet.refresh_from_db()
    generation = Generation.objects.select_related("assistant_message").get(
        owner=user, idempotency_key="preflight:cleanup:normal"
    )
    assert generation.state == Generation.State.FAILED
    assert generation.error_code == "preflight_internal"
    assert generation.completed_at is not None
    assert generation.assistant_message.status == Message.Status.FAILED
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_preflight_wrapper_recovers_reserve_when_first_release_fails(monkeypatch):
    user, conversation = _setup_preflight_case("release-retry")

    def fail_request_cost(*_args, **_kwargs):
        raise RuntimeError("synthetic RequestCost failure after reserve")

    def fail_first_release(_reservation_id):
        raise RuntimeError("synthetic first release failure")

    monkeypatch.setattr(RequestCost.objects, "create", fail_request_cost)
    # The base prepare runtime references streaming.release. The terminal wrapper
    # imports the authoritative billing release separately, so its recovery retry
    # remains real and can prove the frozen-reserve path is closed.
    monkeypatch.setattr(streaming, "release", fail_first_release)

    with pytest.raises(RuntimeError, match="synthetic RequestCost failure after reserve"):
        prepare(
            user=user,
            conversation=conversation,
            content="Проверка повторного освобождения резерва",
            client_message_id=uuid.uuid4(),
            idempotency_key="preflight:cleanup:release-retry",
        )

    user.wallet.refresh_from_db()
    generation = Generation.objects.get(
        owner=user, idempotency_key="preflight:cleanup:release-retry"
    )
    reservation = BalanceReservation.objects.get(
        idempotency_key=f"generation:{generation.id}"
    )
    generation.refresh_from_db(fields=["reservation_id", "state", "error_code"])
    assert generation.reservation_id == reservation.id
    assert generation.state == Generation.State.FAILED
    assert generation.error_code == "preflight_internal"
    assert reservation.state == BalanceReservation.State.RELEASED
    assert user.wallet.available_rub == Decimal("10.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


def test_provider_funding_preflight_has_stable_diagnostic_code():
    exc = ValidationError("Сейчас нет модели с доступным API-балансом для этого запроса")
    assert classify_preflight_exception(exc) == "preflight_provider_funding"
