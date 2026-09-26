from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("agents", "0005_agentwebhooktrigger_agentwebhookdelivery"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="agentschedule",
            old_name="agents_agent_enabled_290322_idx",
            new_name="agt_sched_enabled_next_idx",
        ),
        migrations.RenameIndex(
            model_name="agentwebhooktrigger",
            old_name="agents_webhook_owner_enabled_idx",
            new_name="agt_wh_owner_enabled_idx",
        ),
        migrations.RenameIndex(
            model_name="agentwebhookdelivery",
            old_name="agents_webhook_state_created_idx",
            new_name="agt_wh_state_created_idx",
        ),
    ]
