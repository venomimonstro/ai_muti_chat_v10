import base64

from django.core.exceptions import ValidationError

from apps.files.models import FileAsset

MAX_VISION_FILES = 4
MAX_VISION_BYTES_PER_FILE = 8 * 1024 * 1024
MIME_BY_TYPE = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}


def resolve_vision_assets(*, user, conversation, file_ids):
    ids = [str(item) for item in (file_ids or [])]
    if len(ids) > MAX_VISION_FILES:
        raise ValidationError(f"Можно прикрепить не более {MAX_VISION_FILES} изображений")
    if not ids:
        return []
    if not conversation.project_id:
        raise ValidationError("Изображения можно использовать только внутри проекта")
    assets = list(
        FileAsset.objects.filter(
            id__in=ids,
            owner=user,
            project_id=conversation.project_id,
            deleted_at__isnull=True,
            status__in=[FileAsset.Status.READY, FileAsset.Status.PARTIAL],
            detected_type__in=MIME_BY_TYPE,
        )
    )
    if len(assets) != len(set(ids)):
        raise ValidationError("Одно или несколько изображений недоступны")
    ordered = {str(asset.id): asset for asset in assets}
    result = []
    for file_id in ids:
        asset = ordered[file_id]
        if asset.size_bytes > MAX_VISION_BYTES_PER_FILE:
            raise ValidationError("Изображение слишком большое для vision-запроса")
        result.append(asset)
    return result


def vision_metadata(assets):
    return [
        {
            "file_id": str(asset.id),
            "file_name": asset.original_name,
            "mime_type": MIME_BY_TYPE[asset.detected_type],
            "size_bytes": asset.size_bytes,
            "sha256": asset.sha256,
        }
        for asset in assets
    ]


def generic_vision_content(text: str, assets):
    blocks = [{"type": "text", "text": text}]
    for asset in assets:
        with asset.blob.open("rb") as stream:
            payload = stream.read(MAX_VISION_BYTES_PER_FILE + 1)
        if len(payload) > MAX_VISION_BYTES_PER_FILE:
            raise ValidationError("Изображение слишком большое для vision-запроса")
        blocks.append(
            {
                "type": "image",
                "media_type": MIME_BY_TYPE[asset.detected_type],
                "data": base64.b64encode(payload).decode("ascii"),
                "file_id": str(asset.id),
            }
        )
    return blocks


def attach_vision_to_messages(messages: list[dict], text: str, assets):
    if not assets:
        return messages
    result = [dict(item) for item in messages]
    for index in range(len(result) - 1, -1, -1):
        if result[index].get("role") == "user":
            result[index]["content"] = generic_vision_content(result[index].get("content") or text, assets)
            return result
    result.append({"role": "user", "content": generic_vision_content(text, assets)})
    return result
