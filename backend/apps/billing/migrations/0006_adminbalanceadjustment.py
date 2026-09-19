# Generated manually for immutable administrative wallet corrections.
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("billing", "0005_wallet_solvency_constraints"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminBalanceAdjustment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("direction", models.CharField(choices=[("credit", "Начисление"), ("debit", "Списание")], max_length=8)),
                ("amount_rub", models.DecimalField(decimal_places=4, max_digits=14)),
                ("comment", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("admin", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="balance_adjustments_made", to=settings.AUTH_USER_MODEL)),
                ("ledger_entry", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="admin_adjustment", to="billing.ledgerentry")),
                ("wallet", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="admin_adjustments", to="billing.wallet")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="adminbalanceadjustment",
            constraint=models.CheckConstraint(condition=models.Q(("amount_rub__gt", 0)), name="admin_adjustment_amount_positive"),
        ),
    ]
