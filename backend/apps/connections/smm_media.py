from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx
from django.core.exceptions import ValidationError

from apps.image_studio.models import GeneratedImage, ImageGeneration

from .vk import VK_TIMEOUT_SECONDS, api_call

PEXELS_API_URL = os.getenv("PEXELS_API_URL", "https://api.pexels.com/v1/search")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
MAX_REMOTE_IMAGE_BYTES = max(1_000_000, int(os.getenv("SMM_MAX_REMOTE_IMAGE_BYTES", "12000000")))
ALLOWED_STOCK_HOSTS = {"images.pexels.com"}


def search_free_stock(query: str, *, per_page: int = 12):
    query = str(query or "").strip()
    if not query:
        raise ValidationError("Укажите тему для поиска изображения")
    if not PEXELS_API_KEY:
        raise ValidationError("Бесплатный фотосток не настроен: задайте PEXELS_API_KEY")
    try:
        response = httpx.get(
            PEXELS_API_URL,
            params={"query": query, "per_page": max(1, min(int(per_page), 30)), "orientation": "square"},
            headers={"Authorization": PEXELS_API_KEY},
            timeout=VK_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError("Фотосток временно недоступен") from exc
    result = []
    for item in payload.get("photos") or []:
        if not isinstance(item, dict):
            continue
        src = item.get("src") or {}
        result.append(
            {
                "id": str(item.get("id") or ""),
                "preview_url": str(src.get("medium") or src.get("small") or ""),
                "image_url": str(src.get("large2x") or src.get("large") or src.get("original") or ""),
                "photographer": str(item.get("photographer") or "")[:160],
                "photographer_url": str(item.get("photographer_url") or "")[:500],
                "source_url": str(item.get("url") or "")[:500],
                "alt": str(item.get("alt") or "")[:300],
            }
        )
    return result


def _generated_image_bytes(*, owner, generation_id):
    generation = ImageGeneration.objects.filter(pk=generation_id, owner=owner).first()
    if generation is None or generation.state != ImageGeneration.State.COMPLETED:
        raise ValidationError("Сгенерированное изображение ещё не готово или недоступно")
    image = GeneratedImage.objects.filter(generation=generation).order_by("position").first()
    if image is None:
        raise ValidationError("Генерация завершилась без изображения")
    with image.file.open("rb") as source:
        data = source.read(MAX_REMOTE_IMAGE_BYTES + 1)
    if len(data) > MAX_REMOTE_IMAGE_BYTES:
        raise ValidationError("Изображение слишком большое для публикации")
    return data, image.mime_type or "image/jpeg", f"generated-{image.id}.jpg"


def _stock_image_bytes(url: str):
    url = str(url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_STOCK_HOSTS:
        raise ValidationError("Разрешены только изображения выбранного бесплатного фотостока")
    try:
        with httpx.stream("GET", url, timeout=VK_TIMEOUT_SECONDS, follow_redirects=False) as response:
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValidationError("Фотосток вернул неподдерживаемый формат")
            chunks = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_REMOTE_IMAGE_BYTES:
                    raise ValidationError("Изображение слишком большое для публикации")
                chunks.append(chunk)
    except ValidationError:
        raise
    except httpx.HTTPError as exc:
        raise ValidationError("Не удалось скачать изображение с фотостока") from exc
    extension = "jpg" if content_type == "image/jpeg" else content_type.split("/", 1)[-1]
    return b"".join(chunks), content_type, f"stock.{extension}"


def _upload_photo(connection, *, group_id: str, data: bytes, mime_type: str, filename: str):
    upload = api_call(connection.get_secret(), "photos.getWallUploadServer", {"group_id": group_id}).get("response") or {}
    upload_url = str(upload.get("upload_url") or "").strip()
    if not upload_url.startswith("https://"):
        raise ValidationError("VK не вернул безопасный URL загрузки изображения")
    try:
        response = httpx.post(
            upload_url,
            files={"photo": (filename, data, mime_type)},
            timeout=max(VK_TIMEOUT_SECONDS, 30),
            follow_redirects=False,
        )
        response.raise_for_status()
        uploaded = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError("Не удалось загрузить изображение в VK") from exc
    server = uploaded.get("server")
    photo = uploaded.get("photo")
    photo_hash = uploaded.get("hash")
    if server is None or not photo or not photo_hash:
        raise ValidationError("VK вернул неполный ответ после загрузки изображения")
    saved = api_call(
        connection.get_secret(),
        "photos.saveWallPhoto",
        {"group_id": group_id, "server": server, "photo": photo, "hash": photo_hash},
    ).get("response") or []
    if not saved or not isinstance(saved, list):
        raise ValidationError("VK не сохранил изображение")
    item = saved[0] if isinstance(saved[0], dict) else {}
    owner_id = item.get("owner_id")
    photo_id = item.get("id")
    if owner_id is None or photo_id is None:
        raise ValidationError("VK не вернул идентификатор сохранённого изображения")
    attachment = f"photo{owner_id}_{photo_id}"
    if item.get("access_key"):
        attachment += f"_{item['access_key']}"
    return attachment


def prepare_item_media(item):
    connection = item.plan.connection
    group_id = str((connection.metadata or {}).get("selected_group_id") or "").strip()
    if not group_id:
        raise ValidationError("Сначала выберите сообщество VK")
    if item.media_source == item.MediaSource.GENERATED:
        if not item.media_generation_id:
            raise ValidationError("Сначала создайте изображение")
        data, mime_type, filename = _generated_image_bytes(
            owner=item.plan.owner,
            generation_id=item.media_generation_id,
        )
    elif item.media_source == item.MediaSource.STOCK:
        data, mime_type, filename = _stock_image_bytes(item.media_url)
    else:
        raise ValidationError("Для подготовки VK-вложения выберите AI-изображение или фотосток")
    return _upload_photo(
        connection,
        group_id=group_id,
        data=data,
        mime_type=mime_type,
        filename=filename,
    )
