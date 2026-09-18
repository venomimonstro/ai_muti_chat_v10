from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.chat.models import Conversation, Generation, Message
from apps.image_studio.models import ImageGeneration, ImageModel

from .models import BalanceReservation
from .services import credit, reserve


@pytest.mark.django_db(transaction=True)
def test_recovery_releases_stale_chat_reservation_and_terminalizes_generation():
    user = User.objects.create_user(
        username="stale-chat", email="stale-chat@example.test", password="password123"
    )
    credit(user, Decimal("100"), "test", "stale-chat")
    conversation = Conversation.objects.create(owner=user, title="Stale")
    user_message = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="hello"
    )
    assistant = Message.objects.create(
        conversation=conversation, role=Message.Role.ASSISTANT, content="partial"
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.RUNNING,
        model="test",
        idempotency_key="client-stale-chat",
    )
    reservation = reserve(user, Decimal("25"), f"generation:{generation.id}")
    generation.reservation_id = reservation.id
    generation.save(update_fields=["reservation_id"])
    BalanceReservation.objects.filter(pk=reservation.id).update(
        created_at=timezone.now() - timedelta(hours=2)
    )

    call_command("recover_stale_reservations", older_than_minutes=30)

    generation.refresh_from_db()
    assistant.refresh_from_db()
    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert reservation.state == BalanceReservation.State.RELEASED
    assert generation.state == Generation.State.FAILED
    assert generation.error_code == "stale_reservation_recovered"
    assert assistant.status == Message.Status.PARTIAL
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_recovery_releases_stale_image_reservation_without_charging_customer():
    user = User.objects.create_user(
        username="stale-image", email="stale-image@example.test", password="password123"
    )
    credit(user, Decimal("100"), "test", "stale-image")
    provider = Provider.objects.create(slug="stale-image-provider", name="Image")
    model = ImageModel.objects.create(
        provider=provider,
        slug="stale-image-model",
        display_name="Image",
        upstream_model="image-v1",
        provider_price_per_image=Decimal("1"),
    )
    generation = ImageGeneration.objects.create(
        owner=user,
        model=model,
        prompt="test",
        size="1024x1024",
        quality="standard",
        requested_count=1,
        state=ImageGeneration.State.RUNNING,
        idempotency_key="client-stale-image",
        estimated_cost_rub=Decimal("10"),
    )
    reservation = reserve(user, Decimal("10"), f"image:{generation.id}")
    generation.reservation = reservation
    generation.save(update_fields=["reservation"])
    BalanceReservation.objects.filter(pk=reservation.id).update(
        created_at=timezone.now() - timedelta(hours=2)
    )

    call_command("recover_stale_reservations", older_than_minutes=30)

    generation.refresh_from_db()
    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert reservation.state == BalanceReservation.State.RELEASED
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.error_code == "stale_reservation_recovered"
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_recovery_never_releases_completed_operation_reservation():
    user = User.objects.create_user(
        username="stale-complete", email="stale-complete@example.test", password="password123"
    )
    credit(user, Decimal("100"), "test", "stale-complete")
    conversation = Conversation.objects.create(owner=user, title="Completed")
    user_message = Message.objects.create(
        conversation=conversation, role=Message.Role.USER, content="hello"
    )
    assistant = Message.objects.create(
        conversation=conversation, role=Message.Role.ASSISTANT, content="done"
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.COMPLETED,
        model="test",
        idempotency_key="client-completed",
    )
    reservation = reserve(user, Decimal("25"), f"generation:{generation.id}")
    generation.reservation_id = reservation.id
    generation.save(update_fields=["reservation_id"])
    BalanceReservation.objects.filter(pk=reservation.id).update(
        created_at=timezone.now() - timedelta(hours=2)
    )

    call_command("recover_stale_reservations", older_than_minutes=30)

    reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert reservation.state == BalanceReservation.State.ACTIVE
    assert user.wallet.available_rub == Decimal("75.0000")
    assert user.wallet.reserved_rub == Decimal("25.0000")
