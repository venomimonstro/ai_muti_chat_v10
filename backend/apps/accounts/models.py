import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        USER = "user", "Пользователь"
        ORG_ADMIN = "org_admin", "Администратор организации"
        PLATFORM_ADMIN = "platform_admin", "Администратор платформы"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активен"
        BLOCKED = "blocked", "Заблокирован"
        DELETED = "deleted", "Удалён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.USER)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)


class UserPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences"
    )
    low_balance_threshold_rub = models.DecimalField(max_digits=14, decimal_places=2, default=50)
    daily_spend_limit_rub = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    monthly_spend_limit_rub = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    product_notifications = models.BooleanField(default=True)
    billing_notifications = models.BooleanField(default=True)
    compact_sidebar = models.BooleanField(default=False)
    memory_enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class Notification(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Информация"
        WARNING = "warning", "Предупреждение"
        SUCCESS = "success", "Успешно"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True)
    level = models.CharField(max_length=16, choices=Level.choices, default=Level.INFO)
    action_url = models.CharField(max_length=300, blank=True)
    dedupe_key = models.CharField(max_length=160, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "dedupe_key"],
                condition=~models.Q(dedupe_key=""),
                name="unique_user_notification_dedupe",
            )
        ]


class SupportRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыт"
        IN_PROGRESS = "in_progress", "В работе"
        RESOLVED = "resolved", "Решён"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="support_requests"
    )
    subject = models.CharField(max_length=160)
    message = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
