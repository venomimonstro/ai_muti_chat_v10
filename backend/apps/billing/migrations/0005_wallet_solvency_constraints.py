from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0004_cost_protection")]

    operations = [
        migrations.AddConstraint(
            model_name="wallet",
            constraint=models.CheckConstraint(condition=models.Q(("available_rub__gte", 0)), name="wallet_available_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="wallet",
            constraint=models.CheckConstraint(condition=models.Q(("reserved_rub__gte", 0)), name="wallet_reserved_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="wallet",
            constraint=models.CheckConstraint(condition=models.Q(("paid_rub__gte", 0)), name="wallet_paid_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="wallet",
            constraint=models.CheckConstraint(condition=models.Q(("promo_rub__gte", 0)), name="wallet_promo_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="wallet",
            constraint=models.CheckConstraint(
                condition=models.Q(("available_rub", models.F("paid_rub") + models.F("promo_rub"))),
                name="wallet_available_equals_buckets",
            ),
        ),
        migrations.AddConstraint(
            model_name="balancereservation",
            constraint=models.CheckConstraint(condition=models.Q(("amount_rub__gt", 0)), name="reservation_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="balancereservation",
            constraint=models.CheckConstraint(condition=models.Q(("paid_amount_rub__gte", 0)), name="reservation_paid_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="balancereservation",
            constraint=models.CheckConstraint(condition=models.Q(("promo_amount_rub__gte", 0)), name="reservation_promo_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="balancereservation",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount_rub", models.F("paid_amount_rub") + models.F("promo_amount_rub"))),
                name="reservation_amount_equals_buckets",
            ),
        ),
        migrations.AddConstraint(
            model_name="balancereservation",
            constraint=models.CheckConstraint(
                condition=models.Q(("actual_rub__isnull", True)) | models.Q(("actual_rub__gte", 0)),
                name="reservation_actual_nonnegative",
            ),
        ),
        migrations.AddConstraint(
            model_name="balancereservation",
            constraint=models.CheckConstraint(
                condition=models.Q(("actual_rub__isnull", True)) | models.Q(("actual_rub__lte", models.F("amount_rub"))),
                name="reservation_actual_not_above_reserved",
            ),
        ),
    ]
