from datetime import timedelta
import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import Notification, User

from .models import Conversation, Generation, Message
from .tasks import ALERT_PREFIX, CHAT_HEALTH_LOCK, chat_service_health_watch_task, chat_service_snapshot


@pytest.mark.django_db(transaction=True)
def test_health_watch_notifies_admin_when_customer_has_no_routable_models():
    admin = User.objects.create_user(
        username="chat-health-admin",
        email="chat-health-admin@example.test",
        password="password123!",
        role=User.Role.PLATFORM_ADMIN,
        status=User.Status.ACTIVE,
    )
    cache.delete(CHAT_HEALTH_LOCK)
    cache.delete(f"{ALERT_PREFIX}no-routable-models")

    result = chat_service_health_watch_task.run()

    assert result["status"] == "ok"
    assert result["ready_models"] == 0
    notice = Notification.objects.filter(user=admin).order_by("-created_at").first()
    assert notice is not None
    assert notice.level == Notification.Level.WARNING
    assert notice.title.startswith("КРИТИЧНО · Чат:")
    assert notice.action_url == "/admin-console/chat-diagnostics"


@pytest.mark.django_db(transaction=True)
def test_health_watch_deduplicates_outage_notifications():
    admin = User.objects.create_user(
        username="chat-health-dedupe-admin",
        email="chat-health-dedupe-admin@example.test",
        password="password123!",
        role=User.Role.PLATFORM_ADMIN,
        status=User.Status.ACTIVE,
    )
    cache.delete(CHAT_HEALTH_LOCK)
    cache.delete(f"{ALERT_PREFIX}no-routable-models")

    first = chat_service_health_watch_task.run()
    cache.delete(CHAT_HEALTH_LOCK)
    second = chat_service_health_watch_task.run()

    assert first["alerts_created"] == 1
    assert second["alerts_created"] == 0
    assert Notification.objects.filter(user=admin).count() == 1


@pytest.mark.django_db(transaction=True)
def test_health_snapshot_uses_chat_specific_stale_timeout(settings):
    settings.OPERATION_STALE_TIMEOUT_SECONDS = 900
    settings.CHAT_GENERATION_STALE_TIMEOUT_SECONDS = 60
    user = User.objects.create_user(
        username="chat-health-stale-cutoff",
        email="chat-health-stale-cutoff@example.test",
        password="password123!",
    )
    conversation = Conversation.objects.create(owner=user)
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="stale",
    )
    assistant = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        status=Message.Status.STREAMING,
    )
    generation = Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant,
        state=Generation.State.RUNNING,
        model="stale-model",
        idempotency_key="chat-health-stale-cutoff",
    )
    Generation.objects.filter(pk=generation.pk).update(
        created_at=timezone.now() - timedelta(minutes=2)
    )

    snapshot = chat_service_snapshot()

    assert snapshot["stale_generations"] == 1
