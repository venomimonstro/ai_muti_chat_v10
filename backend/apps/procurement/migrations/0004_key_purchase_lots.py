import uuid

from django.db import migrations, models
import django.db.models.deletion


def backfill_document_numbers(apps, schema_editor):
    ProviderPurchase = apps.get_model("procurement", "ProviderPurchase")
    for purchase in ProviderPurchase.objects.filter(document_number__isnull=True).iterator():
        purchase.document_number = f"API-{str(purchase.id).replace('-', '')[:16].upper()}"
        purchase.save(update_fields=["document_number"])


class Migration(migrations.Migration):
    dependencies = [
        ("ai_registry", "0008_normalize_active_routing_policy"),
        ("procurement", "0003_providerspend_customer_charge"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="providerfundingaccount",
            name="unique_provider_procurement_credential_env",
        ),
        migrations.AlterField(
            model_name="providerfundingaccount",
            name="credential_env",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="providerfundingaccount",
            name="api_key",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="funding_account",
                to="ai_registry.providerapikey",
            ),
        ),
        migrations.AddConstraint(
            model_name="providerfundingaccount",
            constraint=models.UniqueConstraint(
                condition=~models.Q(credential_env=""),
                fields=("provider", "credential_env"),
                name="unique_provider_procurement_credential_env",
            ),
        ),
        # Keep the field plain during the nullable/backfill phase. On PostgreSQL,
        # db_index=True followed by unique=True in the same migration makes Django
        # schedule the same varchar_pattern_ops "_like" index twice.
        migrations.AddField(
            model_name="providerpurchase",
            name="document_number",
            field=models.CharField(max_length=48, null=True),
        ),
        migrations.AddField(
            model_name="providerpurchase",
            name="payment_amount",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True),
        ),
        migrations.AddField(
            model_name="providerpurchase",
            name="payment_currency",
            field=models.CharField(default="RUB", max_length=3),
        ),
        migrations.AddField(
            model_name="providerpurchase",
            name="payment_fx_rate_rub",
            field=models.DecimalField(blank=True, decimal_places=8, max_digits=18, null=True),
        ),
        migrations.RunPython(backfill_document_numbers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="providerpurchase",
            name="document_number",
            field=models.CharField(max_length=48),
        ),
        migrations.AddConstraint(
            model_name="providerpurchase",
            constraint=models.UniqueConstraint(
                fields=("document_number",),
                name="unique_provider_purchase_document_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="providerpurchase",
            constraint=models.CheckConstraint(
                condition=models.Q(payment_amount__isnull=True) | models.Q(payment_amount__gt=0),
                name="provider_purchase_payment_positive",
            ),
        ),
        migrations.CreateModel(
            name="ProviderSpendAllocation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("native_amount", models.DecimalField(decimal_places=6, max_digits=18)),
                ("economic_cost_rub", models.DecimalField(decimal_places=4, max_digits=18)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("purchase", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="spend_allocations", to="procurement.providerpurchase")),
                ("spend", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_allocations", to="procurement.providerspend")),
            ],
            options={"ordering": ["purchase__purchased_at", "created_at"]},
        ),
        migrations.AddConstraint(
            model_name="providerspendallocation",
            constraint=models.UniqueConstraint(fields=("spend", "purchase"), name="unique_spend_purchase_allocation"),
        ),
        migrations.AddConstraint(
            model_name="providerspendallocation",
            constraint=models.CheckConstraint(condition=models.Q(native_amount__gt=0), name="spend_allocation_native_positive"),
        ),
        migrations.AddConstraint(
            model_name="providerspendallocation",
            constraint=models.CheckConstraint(condition=models.Q(economic_cost_rub__gte=0), name="spend_allocation_cost_nonnegative"),
        ),
    ]
