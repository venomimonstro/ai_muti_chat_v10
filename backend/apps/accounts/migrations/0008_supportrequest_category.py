from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0007_user_legal_acceptance")]

    operations = [
        migrations.AddField(
            model_name="supportrequest",
            name="category",
            field=models.CharField(
                choices=[
                    ("billing", "Оплата и баланс"),
                    ("generation", "Ответы AI"),
                    ("files", "Файлы и проекты"),
                    ("account", "Аккаунт"),
                    ("other", "Другое"),
                ],
                default="other",
                max_length=20,
            ),
        ),
    ]
