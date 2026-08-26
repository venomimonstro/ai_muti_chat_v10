import hashlib
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.pricing import calculate_flat_from_snapshot, quote_flat, require_margin
from apps.billing.services import release, reserve, settle

from .adapters import ImageProviderError, _detect_mime, adapter_for
from .models import GeneratedImage, ImageGeneration, ImageModel


def _validated(model_slug, prompt, size, quality, count):
    if not settings.IMAGES_ENABLED:
        raise ValidationError("Генерация изображений временно отключена")
    prompt = str(prompt).strip()
    if not prompt or len(prompt) > settings.IMAGE_MAX_PROMPT_CHARS:
        raise ValidationError("Промпт обязателен и не должен превышать лимит")
    model = ImageModel.objects.select_related("provider").filter(
        slug=model_slug, enabled=True, provider__enabled=True, provider__emergency_disabled=False
    ).first()
    if not model:
        raise ValidationError("Image-модель недоступна")
    if size not in model.supported_sizes or quality not in model.supported_qualities:
        raise ValidationError("Размер или качество не поддерживается моделью")
    try:
        count = int(count)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Количество должно быть целым числом") from exc
    if not 1 <= count <= min(model.max_images, 4):
        raise ValidationError("Недопустимое количество изображений")
    return model, prompt, count


def preview(*, model_slug, prompt, size, quality, count):
    model, prompt, count = _validated(model_slug, prompt, size, quality, count)
    value = require_margin(quote_flat(
        provider_cost_native=model.provider_price_per_image * count,
        provider_currency=model.provider_currency,
        base_markup_percent=model.markup_percent,
        provider_slug=model.provider.slug,
        model_slug=model.slug,
        operation_type="images",
    ))
    return model, value, prompt, count


def _validate_existing_payload(existing, model_slug, prompt, size, quality, count):
    normalized_prompt = str(prompt).strip()
    try:
        normalized_count = int(count)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Количество должно быть целым числом") from exc
    if (
        existing.model.slug != model_slug
        or existing.prompt != normalized_prompt
        or existing.size != size
        or existing.quality != quality
        or existing.requested_count != normalized_count
    ):
        raise ValidationError("Idempotency-Key уже использован для другого запроса")


def _reset_failed_generation(generation):
    if generation.reservation_id:
        release(generation.reservation_id)
    for image in generation.images.all():
        image.file.delete(save=False)
    generation.images.all().delete()
    generation.state = ImageGeneration.State.RUNNING
    generation.error_code = ""
    generation.actual_count = 0
    generation.provider_request_id = ""
    generation.provider_cost_rub = Decimal("0")
    generation.actual_cost_rub = Decimal("0")
    generation.completed_at = None
    generation.reservation = None
    generation.save(
        update_fields=[
            "state",
            "error_code",
            "actual_count",
            "provider_request_id",
            "provider_cost_rub",
            "actual_cost_rub",
            "completed_at",
            "reservation",
        ]
    )


def _execute_generation(generation, model, snapshot, count, adapter=None):
    try:
        result = (adapter or adapter_for(model)).generate(
            model=model.upstream_model, prompt=generation.prompt, size=generation.size,
            quality=generation.quality, count=count,
        )
        if not result.images or len(result.images) > count:
            raise ImageProviderError("Invalid number of images", code="invalid_response")
        validated_images = []
        for item in result.images:
            mime = _detect_mime(item.content)
            if mime != item.mime_type or len(item.content) > settings.IMAGE_MAX_RESULT_BYTES:
                raise ImageProviderError("Unsafe image payload", code="invalid_image")
            validated_images.append((item, mime))
        for position, (item, mime) in enumerate(validated_images):
            extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
            image = GeneratedImage(
                generation=generation, position=position, mime_type=mime,
                size_bytes=len(item.content), sha256=hashlib.sha256(item.content).hexdigest(),
                revised_prompt=item.revised_prompt,
            )
            image.file.save(f"{position}.{extension}", ContentFile(item.content), save=True)
        actual_count = generation.images.count()
        native = Decimal(snapshot["provider_price_per_image"]) * actual_count
        provider_cost, charge, _profit, _margin = calculate_flat_from_snapshot(native, snapshot)
        with transaction.atomic():
            settle(generation.reservation_id, charge)
            generation.state = ImageGeneration.State.COMPLETED
            generation.actual_count = actual_count
            generation.provider_request_id = result.provider_request_id
            generation.provider_cost_rub = provider_cost
            generation.actual_cost_rub = charge
            generation.completed_at = timezone.now()
            generation.save(update_fields=[
                "state", "actual_count", "provider_request_id", "provider_cost_rub",
                "actual_cost_rub", "completed_at",
            ])
    except Exception as exc:
        release(generation.reservation_id)
        for image in generation.images.all():
            image.file.delete(save=False)
        generation.images.all().delete()
        generation.state = ImageGeneration.State.FAILED
        generation.error_code = exc.code if isinstance(exc, ImageProviderError) else "internal_error"
        generation.completed_at = timezone.now()
        generation.save(update_fields=["state", "error_code", "completed_at"])
    return generation


def generate(
    *, user, model_slug, prompt, size, quality, count, idempotency_key,
    confirmed=False, adapter=None
):
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValidationError("Корректный Idempotency-Key обязателен")
    existing = ImageGeneration.objects.filter(owner=user, idempotency_key=idempotency_key).first()
    if existing:
        _validate_existing_payload(existing, model_slug, prompt, size, quality, count)
        if existing.state == ImageGeneration.State.COMPLETED:
            return existing
        if existing.state == ImageGeneration.State.RUNNING:
            return existing
        _reset_failed_generation(existing)
        reservation = reserve(user, existing.estimated_cost_rub, f"image:{existing.id}")
        existing.reservation = reservation
        existing.save(update_fields=["reservation"])
        return _execute_generation(existing, existing.model, existing.price_snapshot, existing.requested_count, adapter)

    model, value, prompt, count = preview(
        model_slug=model_slug, prompt=prompt, size=size, quality=quality, count=count
    )
    if value.user_charge_rub >= Decimal(str(settings.IMAGE_CONFIRM_THRESHOLD_RUB)) and not confirmed:
        raise ValidationError("Подтвердите ожидаемую стоимость генерации")
    snapshot = {
        **value.pricing_snapshot,
        "model_slug": model.slug,
        "provider_slug": model.provider.slug,
        "provider_price_per_image": str(model.provider_price_per_image),
        "requested_count": count,
        "size": size,
        "quality": quality,
    }
    try:
        with transaction.atomic():
            generation = ImageGeneration.objects.create(
                owner=user, model=model, prompt=prompt, size=size, quality=quality,
                requested_count=count, idempotency_key=idempotency_key,
                price_snapshot=snapshot, estimated_cost_rub=value.user_charge_rub,
            )
            reservation = reserve(user, value.user_charge_rub, f"image:{generation.id}")
            generation.reservation = reservation
            generation.save(update_fields=["reservation"])
    except IntegrityError:
        replay = ImageGeneration.objects.get(owner=user, idempotency_key=idempotency_key)
        _validate_existing_payload(replay, model_slug, prompt, size, quality, count)
        if replay.state == ImageGeneration.State.COMPLETED:
            return replay
        if replay.state == ImageGeneration.State.RUNNING:
            return replay
        _reset_failed_generation(replay)
        reservation = reserve(user, replay.estimated_cost_rub, f"image:{replay.id}")
        replay.reservation = reservation
        replay.save(update_fields=["reservation"])
        return _execute_generation(replay, replay.model, replay.price_snapshot, replay.requested_count, adapter)

    return _execute_generation(generation, model, snapshot, count, adapter)
