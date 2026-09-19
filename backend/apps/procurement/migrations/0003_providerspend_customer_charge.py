from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("procurement", "0002_retailtokenpriceversion")]

    operations = [
        migrations.AddField(
            model_name="providerspend",
            name="customer_charge_rub",
            field=models.DecimalField(decimal_places=4, default=0, max_digits=18),
        ),
        migrations.AddConstraint(
            model_name="providerspend",
            constraint=models.CheckConstraint(
                condition=models.Q(("customer_charge_rub__gte", 0)),
                name="provider_spend_customer_charge_nonnegative",
            ),
        ),
    ]
