from celery import shared_task


@shared_task(name="apps.files.tasks.process_file_task", soft_time_limit=600, time_limit=660)
def process_file_task(asset_id: str):
    from .models import FileAsset
    from .services import process_file

    asset = FileAsset.objects.get(pk=asset_id)
    process_file(asset)
