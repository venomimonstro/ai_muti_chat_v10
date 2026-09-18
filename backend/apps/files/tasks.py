from celery import shared_task

from .models import FileAsset
from .services import process_file


@shared_task(bind=True, autoretry_for=(), max_retries=0)
def process_file_task(self, asset_id):
    asset = FileAsset.objects.filter(pk=asset_id, deleted_at__isnull=True).first()
    if asset is None:
        return {"processed": False, "reason": "missing_or_deleted"}
    if asset.status in {
        FileAsset.Status.READY,
        FileAsset.Status.PARTIAL,
        FileAsset.Status.DELETED,
        FileAsset.Status.DELETING,
    }:
        return {"processed": False, "reason": f"state:{asset.status}"}
    process_file(asset)
    asset.refresh_from_db(fields=["status", "error_code"])
    return {"processed": True, "status": asset.status, "error_code": asset.error_code}
