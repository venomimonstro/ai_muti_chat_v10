from django.db import migrations


def make_hubai_admin_only(apps, _schema_editor):
    Provider = apps.get_model("ai_registry", "Provider")
    Provider.objects.filter(slug="hubai").update(
        credential_env="",
        api_base_url="https://hubai.loe.gg/v1",
        adapter_type="deepseek_chat",
    )


class Migration(migrations.Migration):
    dependencies = [("ai_registry", "0016_hubai_provider")]
    operations = [migrations.RunPython(make_hubai_admin_only, migrations.RunPython.noop)]
