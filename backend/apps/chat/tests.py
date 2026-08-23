import uuid
from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderError
from apps.billing.services import credit

from .models import Conversation, Generation, Message
from .services import generate_reply


class FailingAdapter:
    def generate(self, **_kwargs):
        raise ProviderError("upstream unavailable")


@pytest.mark.django_db(transaction=True)
def test_save_before_inference_and_release_on_failure():
    user = User.objects.create_user(
        username="tester", email="t@example.com", password="password123"
    )
    credit(user, 10, "test", "chat")
    conversation = Conversation.objects.create(owner=user)
    generation = generate_reply(
        user=user,
        conversation=conversation,
        content="Не потеряй меня",
        client_message_id=uuid.uuid4(),
        idempotency_key="send:1",
        adapter=FailingAdapter(),
    )
    assert generation.state == Generation.State.FAILED
    assert Message.objects.filter(
        conversation=conversation, role=Message.Role.USER, content="Не потеряй меня"
    ).exists()
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == 10
    assert user.wallet.reserved_rub == 0


@pytest.mark.django_db(transaction=True)
def test_duplicate_send_does_not_duplicate_messages_or_charge():
    user = User.objects.create_user(username="dupe", email="d@example.com", password="password123")
    credit(user, 10, "test", "dupe")
    conversation = Conversation.objects.create(owner=user)
    args = dict(
        user=user,
        conversation=conversation,
        content="Один раз",
        client_message_id=uuid.uuid4(),
        idempotency_key="send:same",
    )
    first = generate_reply(**args)
    second = generate_reply(**args)
    assert first.id == second.id
    assert conversation.messages.count() == 2
    user.wallet.refresh_from_db()
    assert user.wallet.available_rub == Decimal("9.9500")
