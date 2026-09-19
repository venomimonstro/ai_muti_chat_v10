import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("ai_registry", "0005_modelversion_and_provider_families"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProviderFundingAccount",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("label", models.CharField(max_length=160)),
                ("credential_env", models.CharField(max_length=120)),
                ("currency", models.CharField(default="USD", max_length=3)),
                ("active", models.BooleanField(default=True)),
                ("is_default", models.BooleanField(default=False)),
                ("priority", models.PositiveIntegerField(default=100)),
                ("funded_native", models.DecimalField(decimal_places=6, default=0, max_digits=18)),
                ("reserved_native", models.DecimalField(decimal_places=6, default=0, max_digits=18)),
                ("spent_native", models.DecimalField(decimal_places=6, default=0, max_digits=18)),
                ("low_balance_native", models.DecimalField(decimal_places=6, default=0, max_digits=18)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="funding_accounts", to="ai_registry.provider")),
            ],
            options={"ordering": ["provider__name", "priority", "label"]},
        ),
        migrations.CreateModel(
            name="ProviderPurchase",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("credit_native", models.DecimalField(decimal_places=6, max_digits=18)),
                ("base_cost_rub", models.DecimalField(decimal_places=4, max_digits=18)),
                ("fees_rub", models.DecimalField(decimal_places=4, default=0, max_digits=18)),
                ("total_cash_outlay_rub", models.DecimalField(decimal_places=4, max_digits=18)),
                ("market_fx_rate_rub", models.DecimalField(blank=True, decimal_places=8, max_digits=18, null=True)),
                ("effective_cost_rub_per_native", models.DecimalField(decimal_places=8, max_digits=18)),
                ("reference", models.CharField(blank=True, max_length=300)),
                ("purchased_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases", to="procurement.providerfundingaccount")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recorded_provider_purchases", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-purchased_at", "-created_at"]},
        ),
        migrations.CreateModel(
            name="ProviderSpendReservation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("amount_native", models.DecimalField(decimal_places=6, max_digits=18)),
                ("actual_native", models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True)),
                ("source_key", models.CharField(max_length=180, unique=True)),
                ("state", models.CharField(choices=[("active", "Активен"), ("settled", "Закрыт"), ("released", "Освобождён")], default="active", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("settled_at", models.DateTimeField(blank=True, null=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="spend_reservations", to="procurement.providerfundingaccount")),
            ],
        ),
        migrations.CreateModel(
            name="ProviderSpend",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_type", models.CharField(max_length=40)),
                ("source_id", models.CharField(max_length=160)),
                ("model_slug", models.CharField(blank=True, max_length=160)),
                ("provider_request_id", models.CharField(blank=True, max_length=200)),
                ("input_tokens", models.PositiveBigIntegerField(default=0)),
                ("output_tokens", models.PositiveBigIntegerField(default=0)),
                ("native_cost", models.DecimalField(decimal_places=6, max_digits=18)),
                ("nominal_cost_rub", models.DecimalField(decimal_places=4, max_digits=18)),
                ("economic_cost_rub", models.DecimalField(decimal_places=4, max_digits=18)),
                ("acquisition_unit_cost_rub", models.DecimalField(decimal_places=8, max_digits=18)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="spends", to="procurement.providerfundingaccount")),
                ("reservation", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="spend", to="procurement.providerspendreservation")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(model_name="providerfundingaccount", constraint=models.UniqueConstraint(fields=("provider", "credential_env"), name="unique_provider_procurement_credential_env")),
        migrations.AddConstraint(model_name="providerfundingaccount", constraint=models.UniqueConstraint(condition=models.Q(("is_default", True)), fields=("provider",), name="unique_default_funding_account_per_provider")),
        migrations.AddConstraint(model_name="providerfundingaccount", constraint=models.CheckConstraint(condition=models.Q(("funded_native__gte", 0)), name="funding_account_funded_nonnegative")),
        migrations.AddConstraint(model_name="providerfundingaccount", constraint=models.CheckConstraint(condition=models.Q(("reserved_native__gte", 0)), name="funding_account_reserved_nonnegative")),
        migrations.AddConstraint(model_name="providerfundingaccount", constraint=models.CheckConstraint(condition=models.Q(("spent_native__gte", 0)), name="funding_account_spent_nonnegative")),
        migrations.AddConstraint(model_name="providerfundingaccount", constraint=models.CheckConstraint(condition=models.Q(("low_balance_native__gte", 0)), name="funding_account_low_balance_nonnegative")),
        migrations.AddConstraint(model_name="providerfundingaccount", constraint=models.CheckConstraint(condition=models.Q(("funded_native__gte", models.F("reserved_native") + models.F("spent_native"))), name="funding_account_not_overdrawn")),
        migrations.AddConstraint(model_name="providerpurchase", constraint=models.CheckConstraint(condition=models.Q(("credit_native__gt", 0)), name="provider_purchase_credit_positive")),
        migrations.AddConstraint(model_name="providerpurchase", constraint=models.CheckConstraint(condition=models.Q(("base_cost_rub__gte", 0)), name="provider_purchase_base_nonnegative")),
        migrations.AddConstraint(model_name="providerpurchase", constraint=models.CheckConstraint(condition=models.Q(("fees_rub__gte", 0)), name="provider_purchase_fees_nonnegative")),
        migrations.AddConstraint(model_name="providerpurchase", constraint=models.CheckConstraint(condition=models.Q(("total_cash_outlay_rub__gt", 0)), name="provider_purchase_total_positive")),
        migrations.AddConstraint(model_name="providerpurchase", constraint=models.CheckConstraint(condition=models.Q(("effective_cost_rub_per_native__gt", 0)), name="provider_purchase_unit_cost_positive")),
        migrations.AddConstraint(model_name="providerspendreservation", constraint=models.CheckConstraint(condition=models.Q(("amount_native__gt", 0)), name="provider_spend_reserve_positive")),
        migrations.AddConstraint(model_name="providerspendreservation", constraint=models.CheckConstraint(condition=models.Q(("actual_native__isnull", True), ("actual_native__gte", 0), _connector="OR"), name="provider_spend_actual_nonnegative")),
        migrations.AddConstraint(model_name="providerspendreservation", constraint=models.CheckConstraint(condition=models.Q(("actual_native__isnull", True), ("actual_native__lte", models.F("amount_native")), _connector="OR"), name="provider_spend_actual_not_over_reserved")),
        migrations.AddConstraint(model_name="providerspend", constraint=models.UniqueConstraint(fields=("source_type", "source_id"), name="unique_provider_spend_source")),
        migrations.AddConstraint(model_name="providerspend", constraint=models.CheckConstraint(condition=models.Q(("native_cost__gte", 0)), name="provider_spend_native_nonnegative")),
        migrations.AddConstraint(model_name="providerspend", constraint=models.CheckConstraint(condition=models.Q(("nominal_cost_rub__gte", 0)), name="provider_spend_nominal_nonnegative")),
        migrations.AddConstraint(model_name="providerspend", constraint=models.CheckConstraint(condition=models.Q(("economic_cost_rub__gte", 0)), name="provider_spend_economic_nonnegative")),
    ]
