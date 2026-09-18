import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("projects", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GitHubInstallation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("installation_id", models.PositiveBigIntegerField(unique=True)),
                ("account_login", models.CharField(max_length=255)),
                ("account_type", models.CharField(blank=True, max_length=40)),
                ("repository_selection", models.CharField(blank=True, max_length=24)),
                ("permissions", models.JSONField(blank=True, default=dict)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="github_installations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["account_login", "installation_id"]},
        ),
        migrations.CreateModel(
            name="GitHubRepositoryBinding",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("repository_id", models.PositiveBigIntegerField()),
                ("full_name", models.CharField(max_length=255)),
                ("default_branch", models.CharField(default="main", max_length=255)),
                ("private", models.BooleanField(default=True)),
                ("write_enabled", models.BooleanField(default=False)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("installation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="repositories", to="github_integration.githubinstallation")),
                ("project", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="github_repository", to="projects.project")),
            ],
        ),
        migrations.AddConstraint(
            model_name="githubrepositorybinding",
            constraint=models.UniqueConstraint(fields=("installation", "repository_id"), name="unique_github_installation_repository"),
        ),
        migrations.CreateModel(
            name="GitHubOperationLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("action", models.CharField(max_length=48)),
                ("path", models.CharField(blank=True, max_length=1024)),
                ("branch", models.CharField(blank=True, max_length=255)),
                ("success", models.BooleanField(default=False)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("binding", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="operation_log", to="github_integration.githubrepositorybinding")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="githuboperationlog",
            index=models.Index(fields=["binding", "-created_at"], name="github_op_binding_idx"),
        ),
    ]
