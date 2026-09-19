from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0003_refundrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="refundrequest",
            name="idempotency_key",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddConstraint(
            model_name="refundrequest",
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=""),
                fields=("user", "idempotency_key"),
                name="unique_user_refund_request_idempotency",
            ),
        ),
    ]
