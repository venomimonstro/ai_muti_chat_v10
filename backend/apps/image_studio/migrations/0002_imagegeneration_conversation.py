from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0012_conversation_ui"),
        ("image_studio", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="imagegeneration",
            name="conversation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="image_generations",
                to="chat.conversation",
            ),
        ),
        migrations.AddIndex(
            model_name="imagegeneration",
            index=models.Index(
                fields=["conversation", "-created_at"],
                name="imagegen_conv_created_idx",
            ),
        ),
    ]
