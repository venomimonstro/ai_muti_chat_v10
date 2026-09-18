import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("admin_ops", "0003_productevent"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationalDrillEvidence",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("load", "Load smoke"),
                            ("chaos", "Chaos restart"),
                            ("commercial_e2e", "Commercial E2E"),
                            ("provider_outage", "Provider outage"),
                            ("duplicate_webhook", "Duplicate payment webhook"),
                            ("payment_failure", "Payment failure"),
                            ("refund", "Refund flow"),
                            ("stale_recovery", "Stale operation recovery"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("evidence_reference", models.CharField(max_length=400)),
                ("checksum_sha256", models.CharField(blank=True, max_length=64)),
                ("notes", models.TextField(blank=True)),
                ("passed_at", models.DateTimeField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "recorded_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="operational_drill_evidence",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-passed_at"]},
        ),
        migrations.AddIndex(
            model_name="operationaldrillevidence",
            index=models.Index(
                fields=["kind", "passed_at"],
                name="admin_ops_o_kind_874f96_idx",
            ),
        ),
    ]
