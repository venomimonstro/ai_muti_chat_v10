from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("procurement", "0004_key_purchase_lots"),
    ]

    operations = [
        migrations.AddField(
            model_name="providerpurchase",
            name="state",
            field=models.CharField(
                choices=[("active", "Активен"), ("cancelled", "Отменён"), ("deleted", "Удалён")],
                db_index=True,
                default="active",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="providerpurchase",
            name="cancelled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="providerpurchase",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="providerpurchase",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
