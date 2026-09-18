from django.db import models


class SystemIssue(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Открыта"
        INVESTIGATING = "investigating", "Разбираемся"
        RESOLVED = "resolved", "Исправлена"
        IGNORED = "ignored", "Игнорируется"

    fingerprint = models.CharField(max_length=32, primary_key=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    severity = models.CharField(max_length=16, default="critical")
    exception_type = models.CharField(max_length=160)
    summary = models.CharField(max_length=500)
    source = models.CharField(max_length=240, db_index=True)
    method = models.CharField(max_length=16, blank=True)
    task_id = models.CharField(max_length=160, blank=True)
    correlation_id = models.CharField(max_length=160, blank=True, db_index=True)
    user_reference = models.CharField(max_length=64, blank=True, db_index=True)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField(db_index=True)
    occurrences = models.PositiveBigIntegerField(default=1)
    resolution_note = models.TextField(blank=True)
    sample_traceback = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [
            models.Index(fields=["status", "last_seen_at"], name="admin_issue_status_seen_idx"),
        ]
