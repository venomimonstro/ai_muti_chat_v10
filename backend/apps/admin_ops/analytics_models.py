import uuid

from django.conf import settings
from django.db import models


class ProductEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_name = models.CharField(max_length=80, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="product_events",
    )
    session_key = models.CharField(max_length=64, blank=True, db_index=True)
    client_event_id = models.CharField(max_length=80, unique=True)
    source_path = models.CharField(max_length=240, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        app_label = "admin_ops"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["event_name", "created_at"],
                name="admin_ops_p_event_n_7bf562_idx",
            )
        ]
