from django.db import migrations, models
import django.utils.timezone
import uuid


class Migration(migrations.Migration):
    dependencies = [("billing", "0006_adminbalanceadjustment")]

    operations = [
        migrations.CreateModel(
            name="PricingOverheadPolicyVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("tax_percent", models.DecimalField(decimal_places=3, default=0, max_digits=7)),
                ("topup_fee_percent", models.DecimalField(decimal_places=3, default=0, max_digits=7)),
                ("other_expenses_percent", models.DecimalField(decimal_places=3, default=0, max_digits=7)),
                ("refund_withdrawal_percent", models.DecimalField(decimal_places=3, default=0, max_digits=7)),
                ("active", models.BooleanField(default=True)),
                ("effective_from", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("reason", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "billing_pricingoverheadpolicyversion",
                "ordering": ["-effective_from", "-created_at"],
            },
        ),
    ]
