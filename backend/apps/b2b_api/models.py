import uuid

from django.conf import settings
from django.db import models


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True)
    billing_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="funded_organizations",
    )
    monthly_limit_rub = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Владелец"
        ADMIN = "admin", "Администратор"
        BILLING = "billing", "Биллинг"
        DEVELOPER = "developer", "Разработчик"
        MEMBER = "member", "Участник"
        VIEWER = "viewer", "Наблюдатель"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активен"
        SUSPENDED = "suspended", "Приостановлен"
        REMOVED = "removed", "Удалён"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="unique_organization_member"
            )
        ]


class APIKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="api_keys"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_api_keys",
    )
    name = models.CharField(max_length=120)
    prefix = models.CharField(max_length=24, unique=True, db_index=True)
    secret_hash = models.CharField(max_length=64)
    scopes = models.JSONField(default=list)
    allowed_models = models.JSONField(default=list, blank=True)
    allowed_endpoints = models.JSONField(default=list, blank=True)
    monthly_limit_rub = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    rate_limit_per_minute = models.PositiveIntegerField(default=60)
    max_concurrency = models.PositiveIntegerField(default=2)
    ip_allowlist = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class APIUsage(models.Model):
    class State(models.TextChoices):
        RUNNING = "running", "Выполняется"
        COMPLETED = "completed", "Завершён"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="usage"
    )
    api_key = models.ForeignKey(APIKey, on_delete=models.PROTECT, related_name="usage")
    model = models.ForeignKey(
        "ai_registry.AIModel", on_delete=models.PROTECT, related_name="api_usage"
    )
    reservation = models.ForeignKey(
        "billing.BalanceReservation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="api_usage",
    )
    response_id = models.CharField(max_length=80, unique=True)
    idempotency_key = models.CharField(max_length=160, blank=True)
    request_hash = models.CharField(max_length=64)
    endpoint = models.CharField(max_length=80, default="chat.completions")
    state = models.CharField(max_length=16, choices=State.choices, default=State.RUNNING)
    estimated_cost_rub = models.DecimalField(max_digits=14, decimal_places=4)
    provider_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    charged_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    provider_request_id = models.CharField(max_length=160, blank=True)
    response_text = models.TextField(blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    pricing_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["api_key", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_api_key_idempotency",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "created_at"],
                name="b2b_api_api_organiz_4437a8_idx",
            ),
            models.Index(
                fields=["api_key", "state", "created_at"],
                name="b2b_api_api_api_key_cb00ca_idx",
            ),
        ]


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="audit_log"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="organization_audit_log"
    )
    action = models.CharField(max_length=80)
    target_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
