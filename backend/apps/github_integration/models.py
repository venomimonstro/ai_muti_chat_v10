import uuid

from django.conf import settings
from django.db import models


class GitHubInstallation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="github_installations",
    )
    installation_id = models.PositiveBigIntegerField(unique=True)
    account_login = models.CharField(max_length=255)
    account_type = models.CharField(max_length=40, blank=True)
    repository_selection = models.CharField(max_length=24, blank=True)
    permissions = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["account_login", "installation_id"]


class GitHubRepositoryBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="github_repository",
    )
    installation = models.ForeignKey(
        GitHubInstallation,
        on_delete=models.PROTECT,
        related_name="repositories",
    )
    repository_id = models.PositiveBigIntegerField()
    full_name = models.CharField(max_length=255)
    default_branch = models.CharField(max_length=255, default="main")
    private = models.BooleanField(default=True)
    write_enabled = models.BooleanField(default=False)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["installation", "repository_id"],
                name="unique_github_installation_repository",
            )
        ]


class GitHubOperationLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    binding = models.ForeignKey(
        GitHubRepositoryBinding,
        on_delete=models.PROTECT,
        related_name="operation_log",
    )
    action = models.CharField(max_length=48)
    path = models.CharField(max_length=1024, blank=True)
    branch = models.CharField(max_length=255, blank=True)
    success = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["binding", "-created_at"], name="github_op_binding_idx")]
