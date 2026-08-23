import uuid

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
