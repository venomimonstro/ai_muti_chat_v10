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

    candidates = list(
        FileAsset.objects.filter(
            id__in=ids,
            owner=user,
            project_id=conversation.project_id,
            deleted_at__isnull=True,
        )
    )
    if len(candidates) != len(ids):
        raise ValidationError("Один или несколько файлов недоступны")

    by_id = {str(asset.id): asset for asset in candidates}
    ordered = [by_id[file_id] for file_id in ids]
    unusable = [asset for asset in ordered if asset.status != FileAsset.Status.READY]
    if unusable:
        asset = unusable[0]
        if asset.error_code == "pdf_text_layer_missing":
            raise ValidationError(
                f"Файл «{asset.original_name}» похож на скан без текстового слоя. "
                "AI не будет делать вид, что прочитал его; загрузите PDF с текстом или изображение страницы."
            )
        if asset.status in {FileAsset.Status.UPLOADED, FileAsset.Status.QUARANTINE, FileAsset.Status.PARSING}:
            raise ValidationError(
                f"Файл «{asset.original_name}» ещё обрабатывается. Дождитесь статуса «Готов»."
            )
        raise ValidationError(
            f"Файл «{asset.original_name}» не удалось подготовить для AI"
            + (f" ({asset.error_code})" if asset.error_code else "")
        )

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
