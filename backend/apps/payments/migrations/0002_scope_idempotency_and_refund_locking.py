from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="idempotency_key",
            field=models.CharField(max_length=64),
        ),
        migrations.AlterField(
            model_name="refund",
            name="idempotency_key",
            field=models.CharField(max_length=64),
        ),
        migrations.AddConstraint(
            model_name="payment",
            constraint=models.UniqueConstraint(
                fields=("user", "idempotency_key"),
                name="unique_user_payment_idempotency",
            ),
        ),
        migrations.AddConstraint(
            model_name="refund",
            constraint=models.UniqueConstraint(
                fields=("payment", "idempotency_key"),
                name="unique_payment_refund_idempotency",
            ),
        ),
    ]
