from django.db import migrations, models


def migrate_balanced_to_auto(apps, schema_editor):
    Conversation = apps.get_model("chat", "Conversation")
    Conversation.objects.filter(routing_mode="balanced").update(routing_mode="auto")


def reverse_auto_to_balanced(apps, schema_editor):
    Conversation = apps.get_model("chat", "Conversation")
    Conversation.objects.filter(routing_mode="auto").update(routing_mode="balanced")


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0015_normalize_public_manual_chats"),
    ]

    operations = [
        migrations.AlterField(
            model_name="conversation",
            name="routing_mode",
            field=models.CharField(
                choices=[
                    ("auto", "AUTO"),
                    ("manual", "Модель"),
                    ("economy", "Простой"),
                    ("balanced", "Средний"),
                    ("maximum", "Сложный"),
                ],
                default="auto",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="routingdecision",
            name="mode",
            field=models.CharField(
                choices=[
                    ("auto", "AUTO"),
                    ("manual", "Модель"),
                    ("economy", "Простой"),
                    ("balanced", "Средний"),
                    ("maximum", "Сложный"),
                ],
                max_length=16,
            ),
        ),
        migrations.RunPython(migrate_balanced_to_auto, reverse_auto_to_balanced),
    ]
