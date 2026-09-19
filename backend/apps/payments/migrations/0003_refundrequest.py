# Generated manually for commercial refund workflow.
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("payments", "0002_scope_idempotency_and_refund_locking"),
    ]

    operations = [
        migrations.CreateModel(
            name="RefundRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("amount_rub", models.DecimalField(decimal_places=2, max_digits=14)),
                ("reason", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("pending", "Ожидает рассмотрения"), ("approved", "Одобрен"), ("processing", "Возврат выполняется"), ("succeeded", "Возврат завершён"), ("rejected", "Отклонён"), ("failed", "Ошибка возврата")], default="pending", max_length=16)),
                ("admin_comment", models.TextField(blank=True)),
                ("held_at", models.DateTimeField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="refund_requests", to="payments.payment")),
                ("refund", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="request", to="payments.refund")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="refund_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="refundrequest",
            constraint=models.CheckConstraint(condition=models.Q(("amount_rub__gt", 0)), name="refund_request_amount_positive"),
        ),
    ]
