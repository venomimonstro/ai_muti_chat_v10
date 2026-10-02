from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("connections", "0003_smm_studio")]
    operations = [migrations.AlterField(model_name="externalconnection", name="kind", field=models.CharField(choices=[("http", "HTTP API"), ("wordpress", "WordPress"), ("vk", "ВКонтакте")], max_length=32))]
