import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Notification, SupportRequest, User


@pytest.mark.django_db
def test_admin_reply_is_visible_to_user_and_creates_notification(settings):
    settings.ADMIN_MFA_ENFORCED = False
    user = User.objects.create_user(
        username="support-user",
        email="support-user@example.test",
        password="password123!",
    )
    admin = User.objects.create_user(
        username="support-admin",
        email="support-admin@example.test",
        password="password123!",
        role=User.Role.PLATFORM_ADMIN,
        is_staff=True,
    )
    ticket = SupportRequest.objects.create(
        user=user,
        subject="Не проходит оплата",
        category=SupportRequest.Category.BILLING,
        message="Платёж вернулся без пополнения.",
    )

    admin_client = APIClient()
    admin_client.force_authenticate(admin)
    response = admin_client.post(
        f"/api/v1/admin/support/{ticket.id}/status/",
        {
            "status": SupportRequest.Status.RESOLVED,
            "reply": "Проверили платёж. Баланс зачислен, повторно платить не нужно.",
        },
        format="json",
    )
    assert response.status_code == 200

    ticket.refresh_from_db()
    assert ticket.status == SupportRequest.Status.RESOLVED
    assert ticket.replied_by == admin
    assert ticket.replied_at is not None
    assert "Баланс зачислен" in ticket.admin_reply
    notification = Notification.objects.get(user=user, action_url="/app/help")
    assert "Поддержка ответила" in notification.title

    user_client = APIClient()
    user_client.force_authenticate(user)
    response = user_client.get("/api/v1/auth/support/")
    assert response.status_code == 200
    payload = response.json()[0]
    assert payload["status"] == SupportRequest.Status.RESOLVED
    assert payload["admin_reply"] == ticket.admin_reply
    assert payload["replied_at"] is not None
