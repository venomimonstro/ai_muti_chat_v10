from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Notification, SupportRequest, User

from .tasks import support_sla_watch_task


@pytest.mark.django_db
def test_support_sla_watch_notifies_admin_and_deduplicates(monkeypatch):
    monkeypatch.setenv("SUPPORT_MAX_UNANSWERED_HOURS", "48")
    admin = User.objects.create_user(
        username="sla-admin",
        email="sla-admin@example.test",
        password="password123",
        role=User.Role.PLATFORM_ADMIN,
    )
    customer = User.objects.create_user(
        username="sla-customer",
        email="sla-customer@example.test",
        password="password123",
    )
    request = SupportRequest.objects.create(
        user=customer,
        subject="Не работает генерация",
        message="Помогите",
    )
    SupportRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(hours=30)
    )

    first = support_sla_watch_task.run()
    second = support_sla_watch_task.run()

    assert first["warnings_created"] == 1
    assert second["warnings_created"] == 0
    item = Notification.objects.get(user=admin)
    assert item.action_url == "/admin-console/support"
    assert "SLA" in item.title


@pytest.mark.django_db
def test_support_sla_watch_creates_overdue_stage(monkeypatch):
    monkeypatch.setenv("SUPPORT_MAX_UNANSWERED_HOURS", "48")
    admin = User.objects.create_user(
        username="sla-admin-overdue",
        email="sla-admin-overdue@example.test",
        password="password123",
        role=User.Role.PLATFORM_ADMIN,
    )
    customer = User.objects.create_user(
        username="sla-customer-overdue",
        email="sla-customer-overdue@example.test",
        password="password123",
    )
    request = SupportRequest.objects.create(
        user=customer,
        subject="Оплата",
        message="Платёж не отобразился",
    )
    SupportRequest.objects.filter(pk=request.pk).update(
        created_at=timezone.now() - timedelta(hours=60)
    )

    result = support_sla_watch_task.run()

    assert result["overdue_created"] == 1
    item = Notification.objects.get(user=admin)
    assert "Просрочено" in item.title
