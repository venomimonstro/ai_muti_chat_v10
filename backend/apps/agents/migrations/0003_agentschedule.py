from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("agents", "0002_agentteam_kind"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentSchedule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=160)),
                ("objective", models.TextField(blank=True)),
                ("enabled", models.BooleanField(default=True)),
                ("interval_minutes", models.PositiveIntegerField(default=1440)),
                ("next_run_at", models.DateTimeField(db_index=True)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("skip_if_running", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("agent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="schedules", to="agents.agent")),
                ("last_run", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="triggered_schedules", to="agents.agentrun")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="agent_schedules", to=settings.AUTH_USER_MODEL)),
                ("team", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="schedules", to="agents.agentteam")),
            ],
            options={"ordering": ["next_run_at", "name"]},
        ),
        migrations.AddIndex(
            model_name="agentschedule",
            index=models.Index(fields=["enabled", "next_run_at"], name="agents_agent_enabled_290322_idx"),
        ),
        migrations.AddConstraint(
            model_name="agentschedule",
            constraint=models.CheckConstraint(
                condition=(models.Q(("agent__isnull", False), ("team__isnull", True)) | models.Q(("agent__isnull", True), ("team__isnull", False))),
                name="agent_schedule_exactly_one_subject",
            ),
        ),
    ]
