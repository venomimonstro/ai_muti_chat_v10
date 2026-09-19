import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("procurement", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RetailTokenPriceVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("model_slug", models.SlugField(db_index=True, max_length=160)),
                ("input_rub_per_million", models.DecimalField(decimal_places=4, max_digits=18)),
                ("output_rub_per_million", models.DecimalField(decimal_places=4, max_digits=18)),
                ("active", models.BooleanField(default=True)),
                ("effective_from", models.DateTimeField(db_index=True)),
                ("reason", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="retail_token_price_versions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["model_slug", "-effective_from", "-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="retailtokenpriceversion",
            constraint=models.CheckConstraint(condition=models.Q(("input_rub_per_million__gt", 0)), name="retail_input_price_positive"),
        ),
        migrations.AddConstraint(
            model_name="retailtokenpriceversion",
            constraint=models.CheckConstraint(condition=models.Q(("output_rub_per_million__gt", 0)), name="retail_output_price_positive"),
        ),
    ]
