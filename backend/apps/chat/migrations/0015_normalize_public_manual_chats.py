from django.db import migrations


def normalize_manual_chats(apps, schema_editor):
    Conversation = apps.get_model("chat", "Conversation")
    # The customer Workspace now exposes product tiers only. Preserve messages,
    # branches and history, but remove stale provider/model coupling from existing
    # manual chats so the visible System Pro state equals the actual next route.
    Conversation.objects.filter(routing_mode="manual").update(
        routing_mode="balanced",
        selected_model="echo-v1",
    )


class Migration(migrations.Migration):
    dependencies = [("chat", "0014_repair_legacy_echo_conversations")]
    operations = [migrations.RunPython(normalize_manual_chats, migrations.RunPython.noop)]
