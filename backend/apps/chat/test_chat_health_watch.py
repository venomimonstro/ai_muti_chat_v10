import pytest
from django.core.cache import cache

from apps.accounts.models import Notification, User

from .tasks import ALERT_PREFIX, CHAT_HEALTH_LOCK, chat_service_health_watch_task


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
