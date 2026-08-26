from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def populate_owners(apps, schema_editor):
    Generation = apps.get_model("chat", "Generation")
    CompareRun = apps.get_model("chat", "CompareRun")
    for generation in Generation.objects.select_related("user_message__conversation").iterator():
        generation.owner_id = generation.user_message.conversation.owner_id
        generation.save(update_fields=["owner"])
    for compare_run in CompareRun.objects.select_related("conversation").iterator():
        compare_run.owner_id = compare_run.conversation.owner_id
        compare_run.save(update_fields=["owner"])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("chat", "0010_compare_and_branches"),
    ]

    operations = [
        migrations.AddField(
            model_name="generation",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="generations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="comparerun",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="compare_runs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="generation",
            name="idempotency_key",
            field=models.CharField(max_length=160),
        ),
        migrations.AlterField(
            model_name="comparerun",
            name="idempotency_key",
            field=models.CharField(max_length=160),
        ),
        migrations.RunPython(populate_owners, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="generation",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="generations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="comparerun",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="compare_runs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="generation",
            constraint=models.UniqueConstraint(
                fields=("owner", "idempotency_key"),
                name="unique_user_generation_idempotency",
            ),
        ),
        migrations.AddConstraint(
            model_name="comparerun",
            constraint=models.UniqueConstraint(
                fields=("owner", "idempotency_key"),
                name="unique_user_compare_idempotency",
            ),
        ),
    ]
