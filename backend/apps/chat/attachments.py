from django.core.exceptions import ValidationError

from apps.files.models import FileAsset

from .vision import MIME_BY_TYPE, MAX_VISION_BYTES_PER_FILE, MAX_VISION_FILES

MAX_CHAT_ATTACHMENTS = 8


def resolve_chat_attachments(*, user, conversation, file_ids):
    ids = [str(item) for item in (file_ids or [])]
    if len(ids) != len(set(ids)):
        raise ValidationError("Один и тот же файл нельзя прикрепить к запросу дважды")
    if len(ids) > MAX_CHAT_ATTACHMENTS:
        raise ValidationError(f"Можно прикрепить не более {MAX_CHAT_ATTACHMENTS} файлов")
    if not ids:
        return [], []
    if not conversation.project_id:
        raise ValidationError("Файлы можно использовать в чате только внутри проекта")

    assets = list(
        FileAsset.objects.filter(
            id__in=ids,
            owner=user,
            project_id=conversation.project_id,
            deleted_at__isnull=True,
            status__in=[FileAsset.Status.READY, FileAsset.Status.PARTIAL],
        )
    )
    if len(assets) != len(ids):
        raise ValidationError("Один или несколько файлов недоступны")

    by_id = {str(asset.id): asset for asset in assets}
    ordered = [by_id[file_id] for file_id in ids]
    vision = [asset for asset in ordered if asset.detected_type in MIME_BY_TYPE]
    if len(vision) > MAX_VISION_FILES:
        raise ValidationError(f"Можно прикрепить не более {MAX_VISION_FILES} изображений")
    for asset in vision:
        if asset.size_bytes > MAX_VISION_BYTES_PER_FILE:
            raise ValidationError("Изображение слишком большое для vision-запроса")
    return ordered, vision


def attachment_metadata(assets):
    return [
        {
            "file_id": str(asset.id),
            "file_name": asset.original_name,
            "detected_type": asset.detected_type,
            "size_bytes": asset.size_bytes,
            "sha256": asset.sha256,
        }
        for asset in assets
    ]
