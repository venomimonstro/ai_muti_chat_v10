import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def bootstrap_cost_protection(apps, _schema_editor):
    fx = apps.get_model("billing", "FxRateSnapshot")
    markup = apps.get_model("billing", "MarkupRuleVersion")
    margin = apps.get_model("billing", "MarginPolicyVersion")
    price = apps.get_model("billing", "PriceVersion")
    now = timezone.now()
    fx.objects.create(
        base_currency="RUB",
        quote_currency="RUB",
        rate="1.00000000",
        source="system_identity",
        effective_at=now,
    )
    markup.objects.create(
        scope_type="global",
        scope_key="",
        markup_percent="100.000",
        price_multiplier="1.0000",
        effective_from=now,
        reason="Sprint 18 default global markup",
    )
    margin.objects.create(
        minimum_gross_margin_percent="25.000",
        anomaly_cost_deviation_percent="20.000",
        reconciliation_threshold_rub="1.0000",
        effective_from=now,
    )
    for item in price.objects.all().iterator():
        price.objects.filter(pk=item.pk).update(
            provider_currency="RUB",
            input_price_per_million=item.input_rub_per_million,
            output_price_per_million=item.output_rub_per_million,
        )


class Migration(migrations.Migration):
    dependencies = [("billing", "0003_balancereservation_paid_amount_rub_and_more")]
    operations = [
        migrations.CreateModel(
            name="FxRateSnapshot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("base_currency", models.CharField(max_length=3)),
                ("quote_currency", models.CharField(default="RUB", max_length=3)),
                ("rate", models.DecimalField(decimal_places=8, max_digits=18)),
                ("source", models.CharField(max_length=80)),
                ("source_reference", models.CharField(blank=True, max_length=240)),
                ("effective_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-effective_at", "-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="fxratesnapshot",
            constraint=models.UniqueConstraint(fields=("base_currency", "quote_currency", "source", "effective_at"), name="unique_fx_rate_source_moment"),
        ),
        migrations.CreateModel(
            name="MarginPolicyVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("minimum_gross_margin_percent", models.DecimalField(decimal_places=3, default=25, max_digits=7)),
                ("anomaly_cost_deviation_percent", models.DecimalField(decimal_places=3, default=20, max_digits=7)),
                ("reconciliation_threshold_rub", models.DecimalField(decimal_places=4, default=1, max_digits=14)),
                ("active", models.BooleanField(default=True)),
                ("effective_from", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-effective_from", "-created_at"]},
        ),
        migrations.CreateModel(
            name="MarkupRuleVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("scope_type", models.CharField(choices=[("global", "Глобальная"), ("provider", "Провайдер"), ("model", "Модель"), ("operation", "Операция"), ("organization", "Организация"), ("contract", "Договор")], max_length=20)),
                ("scope_key", models.CharField(blank=True, max_length=120)),
                ("markup_percent", models.DecimalField(blank=True, decimal_places=3, max_digits=7, null=True)),
                ("price_multiplier", models.DecimalField(decimal_places=4, default=1, max_digits=8)),
                ("active", models.BooleanField(default=True)),
                ("effective_from", models.DateTimeField(db_index=True)),
                ("reason", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-effective_from", "-created_at"]},
        ),
        migrations.AddIndex(
            model_name="markupruleversion",
            index=models.Index(fields=["scope_type", "scope_key", "effective_from"], name="billing_mar_scope_t_cf7296_idx"),
        ),
        migrations.CreateModel(
            name="BillingReconciliationRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("running", "Выполняется"), ("succeeded", "Завершено"), ("failed", "Ошибка")], default="running", max_length=16)),
                ("checked_wallets", models.PositiveIntegerField(default=0)),
                ("checked_requests", models.PositiveIntegerField(default=0)),
                ("discrepancy_count", models.PositiveIntegerField(default=0)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.CreateModel(
            name="BillingReconciliationItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("entity_type", models.CharField(max_length=32)),
                ("entity_id", models.CharField(max_length=128)),
                ("status", models.CharField(choices=[("ok", "OK"), ("undercharged", "Недосписание"), ("overcharged", "Пересписание"), ("provider_mismatch", "Расхождение провайдера"), ("manual_review", "Ручная проверка")], max_length=24)),
                ("expected_rub", models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
                ("actual_rub", models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="billing.billingreconciliationrun")),
            ],
            options={"ordering": ["entity_type", "entity_id"]},
        ),
        migrations.AddField(model_name="priceversion", name="provider_currency", field=models.CharField(default="RUB", max_length=3)),
        migrations.AddField(model_name="priceversion", name="input_price_per_million", field=models.DecimalField(blank=True, decimal_places=6, max_digits=14, null=True)),
        migrations.AddField(model_name="priceversion", name="output_price_per_million", field=models.DecimalField(blank=True, decimal_places=6, max_digits=14, null=True)),
        migrations.AddField(model_name="requestcost", name="expected_provider_cost_rub", field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
        migrations.AddField(model_name="requestcost", name="fx_snapshot", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="billing.fxratesnapshot")),
        migrations.AddField(model_name="requestcost", name="gross_margin_percent", field=models.DecimalField(blank=True, decimal_places=3, max_digits=7, null=True)),
        migrations.AddField(model_name="requestcost", name="gross_profit_rub", field=models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
        migrations.AddField(model_name="requestcost", name="model_version_id_snapshot", field=models.UUIDField(blank=True, null=True)),
        migrations.AddField(model_name="requestcost", name="operation_type", field=models.CharField(default="chat", max_length=32)),
        migrations.AddField(model_name="requestcost", name="pricing_snapshot", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="requestcost", name="reconciliation_status", field=models.CharField(choices=[("pending", "Ожидает сверки"), ("ok", "Сверено"), ("undercharged", "Недосписание"), ("overcharged", "Пересписание"), ("provider_mismatch", "Расхождение провайдера"), ("manual_review", "Ручная проверка")], default="pending", max_length=24)),
        migrations.CreateModel(
            name="CostAnomaly",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("margin_floor", "Маржа ниже floor"), ("cost_deviation", "Отклонение себестоимости"), ("ledger_mismatch", "Расхождение ledger"), ("request_mismatch", "Расхождение запроса"), ("provider_mismatch", "Расхождение провайдера")], max_length=32)),
                ("status", models.CharField(choices=[("open", "Открыта"), ("acknowledged", "Принята"), ("resolved", "Закрыта")], default="open", max_length=16)),
                ("severity", models.CharField(default="warning", max_length=16)),
                ("dedupe_key", models.CharField(max_length=180, unique=True)),
                ("model_slug", models.CharField(blank=True, max_length=120)),
                ("provider_slug", models.CharField(blank=True, max_length=120)),
                ("expected_rub", models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
                ("actual_rub", models.DecimalField(blank=True, decimal_places=4, max_digits=14, null=True)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("request_cost", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="anomalies", to="billing.requestcost")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.RunPython(bootstrap_cost_protection, migrations.RunPython.noop),
    ]
