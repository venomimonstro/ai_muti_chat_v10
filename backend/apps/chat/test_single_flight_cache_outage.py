from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.admin_ops import recovery as recovery_module

from . import single_flight
from .models import Conversation, Generation, Message


@pytest.mark.django_db(transaction=True)
def test_prepare_uses_database_single_flight_without_cache_dependency(monkeypatch):
    user = User.objects.create_user(
        username="db-single-flight",
        email="db-single-flight@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="DB single flight")
    calls = []
    locks = []

    def raw_prepare(**kwargs):
        calls.append(kwargs["idempotency_key"])
        return "prepared", True

    @contextmanager
    def fake_lock(conversation_id):
        locks.append(("enter", str(conversation_id)))
        try:
            yield
        finally:
            locks.append(("exit", str(conversation_id)))

    module = SimpleNamespace(prepare=raw_prepare)
    monkeypatch.setattr(single_flight, "_conversation_lock", fake_lock)
    single_flight.install(module)

    result = module.prepare(
        user=user,
        conversation=conversation,
        content="Проверка без зависимости от Redis",
        client_message_id=uuid.uuid4(),
        idempotency_key="db-single-flight-prepare",
        file_ids=[],
    )

    assert result == ("prepared", True)
    assert calls == ["db-single-flight-prepare"]
    assert locks == [
        ("enter", str(conversation.id)),
        ("exit", str(conversation.id)),
    ]
    assert not hasattr(single_flight, "cache")


@pytest.mark.django_db(transaction=True)
def test_single_flight_blocks_second_active_generation_before_provider_prepare():
    user = User.objects.create_user(
        username="single-flight-active",
        email="single-flight-active@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Active generation")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Первый запрос",
        client_message_id=uuid.uuid4(),
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
    )
    Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.RUNNING,
        model="system-pro",
        idempotency_key="already-running",
    )

    module = SimpleNamespace(prepare=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not run")))
    single_flight.install(module)

    with pytest.raises(ValidationError, match="Предыдущий ответ ещё формируется"):
        module.prepare(
            user=user,
            conversation=conversation,
            content="Второй запрос",
            client_message_id=uuid.uuid4(),
            idempotency_key="second-request",
            file_ids=[],
        )


@pytest.mark.django_db(transaction=True)
def test_single_flight_recovers_already_stale_generation_before_busy_error(monkeypatch):
    user = User.objects.create_user(
        username="single-flight-stale",
        email="single-flight-stale@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user, title="Stale generation")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Зависший запрос",
        client_message_id=uuid.uuid4(),
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
    )
    stale = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.RUNNING,
        model="system-pro",
        idempotency_key="stale-running",
    )
    recovered = []
    raw_calls = []

    # Make the already-created row eligible for the same authoritative stale cutoff
    # without waiting in the regression suite. The fake recovery represents the
    # tested admin_ops authority and leaves single_flight responsible only for wiring.
    monkeypatch.setattr(recovery_module, "_cutoff", lambda: timezone.now() + timedelta(seconds=1))

    def fake_recover(pk):
        recovered.append(pk)
        Generation.objects.filter(pk=pk).update(
            state=Generation.State.FAILED,
            error_code="stale_operation_recovered",
            completed_at=timezone.now(),
        )
        return True

    monkeypatch.setattr(recovery_module, "_recover_generation", fake_recover)

    def raw_prepare(**kwargs):
        raw_calls.append(kwargs["idempotency_key"])
        return "prepared-after-recovery", True

    module = SimpleNamespace(prepare=raw_prepare)
    single_flight.install(module)

    result = module.prepare(
        user=user,
        conversation=conversation,
        content="Новый запрос после восстановления",
        client_message_id=uuid.uuid4(),
        idempotency_key="after-stale-recovery",
        file_ids=[],
    )

    assert result == ("prepared-after-recovery", True)
    assert recovered == [stale.id]
    assert raw_calls == ["after-stale-recovery"]
    stale.refresh_from_db()
    assert stale.state == Generation.State.FAILED
