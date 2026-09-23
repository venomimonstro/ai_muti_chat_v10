from django.db import migrations


def repair_legacy_echo_conversations(apps, schema_editor):
    Conversation = apps.get_model("chat", "Conversation")
    Conversation.objects.filter(
        routing_mode="manual",
        selected_model="echo-v1",
    ).update(routing_mode="balanced")


class Migration(migrations.Migration):
    dependencies = [("chat", "0013_conversation_ui_deleted_at")]
    operations = [migrations.RunPython(repair_legacy_echo_conversations, migrations.RunPython.noop)]
