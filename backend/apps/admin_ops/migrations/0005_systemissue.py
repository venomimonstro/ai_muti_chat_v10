from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("admin_ops", "0004_operational_drill_evidence"),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemIssue",
            fields=[
                ("fingerprint", models.CharField(max_length=32, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("open", "Открыта"), ("investigating", "Разбираемся"), ("resolved", "Исправлена"), ("ignored", "Игнорируется")], db_index=True, default="open", max_length=16)),
                ("severity", models.CharField(default="critical", max_length=16)),
                ("exception_type", models.CharField(max_length=160)),
                ("summary", models.CharField(max_length=500)),
                ("source", models.CharField(db_index=True, max_length=240)),
                ("method", models.CharField(blank=True, max_length=16)),
                ("task_id", models.CharField(blank=True, max_length=160)),
                ("correlation_id", models.CharField(blank=True, db_index=True, max_length=160)),
                ("user_reference", models.CharField(blank=True, db_index=True, max_length=64)),
                ("first_seen_at", models.DateTimeField()),
                ("last_seen_at", models.DateTimeField(db_index=True)),
                ("occurrences", models.PositiveBigIntegerField(default=1)),
                ("resolution_note", models.TextField(blank=True)),
                ("sample_traceback", models.TextField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-last_seen_at"]},
        ),
        migrations.AddIndex(
            model_name="systemissue",
            index=models.Index(fields=["status", "last_seen_at"], name="admin_issue_status_seen_idx"),
        ),
    ]
