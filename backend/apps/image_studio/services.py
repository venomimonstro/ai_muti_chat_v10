import hashlib
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import CostAnomaly
from apps.billing.pricing import calculate_flat_from_snapshot, quote_flat, require_margin
from apps.billing.services import release, reserve, settle
from apps.files.models import FileAsset
from apps.procurement.account_routing import reserve_provider_spend
from apps.procurement.models import ProviderSpendReservation

from .adapters import ImageProviderError, _detect_mime, adapter_for
from .models import GeneratedImage, ImageGeneration, ImageModel
from .quality import record_image_quality_failure
from .validation import validate_generated_image

EDIT_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "png", "jpeg", "jpg", "webp"}


def provider_unit_price(model, size, quality):
    """Return reviewed provider cost for an exact image variant."""
    matrix = model.provider_price_matrix or {}
    key = f"{size}|{quality}"
    raw = matrix.get(key)
    if raw not in {None, ""}:
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError(f"Некорректная себестоимость варианта изображения {key}") from exc
        if value <= 0:
            raise ValidationError(f"Себестоимость варианта изображения {key} должна быть больше нуля")
        return value
    if len(model.supported_sizes or []) == 1 and len(model.supported_qualities or []) == 1:
        if model.provider_price_per_image and model.provider_price_per_image > 0:
            return Decimal(model.provider_price_per_image)
    raise ValidationError(
        f"Не настроена себестоимость изображения для размера {size} и качества {quality}"
    )


def image_price_matrix_complete(model):
    try:
        for size in model.supported_sizes or []:
            for quality in model.supported_qualities or []:
                provider_unit_price(model, size, quality)
    except ValidationError:
        return False
    return bool(model.supported_sizes and model.supported_qualities)


def _trip_image_provider(*, model, generation, reason, expected=None, actual=None):
    CostAnomaly.objects.get_or_create(
        dedupe_key=f"image-provider-contract:{generation.id}:{reason}",
        defaults={
            "kind": CostAnomaly.Kind.COST_DEVIATION,
            "severity": "critical",
            "model_slug": model.slug,
            "provider_slug": model.provider.slug,
            "expected_rub": expected,
            "actual_rub": actual,
            "details": {
                "reason": reason,
                "generation_id": str(generation.id),
                "requested_count": generation.requested_count,
                "scope": "image_model",
            },
        },
    )
    # A malformed count/price contract is scoped to this image model. Do not take
    # text/chat or healthy sibling image models of the same provider offline.
    ImageModel.objects.filter(pk=model.pk).update(enabled=False)
    model.enabled = False


def _validate_existing(existing, *, model_slug, prompt, size, quality, count, conversation, operation="generate", source_file=None):
    normalized_prompt = str(prompt).strip()
    try:
        normalized_count = int(count)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Количество должно быть целым числом") from exc
    snapshot = existing.price_snapshot or {}
    existing_operation = snapshot.get("operation", "generate")
    existing_source = snapshot.get("source_file_id")
    requested_source = str(source_file.id) if source_file else None
    if (
        existing.model.slug != model_slug
        or existing.prompt != normalized_prompt
        or existing.size != size
        or existing.quality != quality
        or existing.requested_count != normalized_count
        or existing.conversation_id != (conversation.id if conversation else None)
        or existing_operation != operation
        or existing_source != requested_source
    ):
        raise ValidationError("Idempotency-Key уже использован для другого запроса")
    return existing


def _validated(model_slug, prompt, size, quality, count):
    if not settings.IMAGES_ENABLED:
        raise ValidationError("Генерация изображений временно отключена")
    prompt = str(prompt).strip()
    if not prompt or len(prompt) > settings.IMAGE_MAX_PROMPT_CHARS:
        raise ValidationError("Промпт обязателен и не должен превышать лимит")
    model = ImageModel.objects.select_related("provider").filter(
        slug=model_slug,
        enabled=True,
        provider__enabled=True,
        provider__emergency_disabled=False,
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


def validate_edit_source(*, user, source_file):
    if source_file is None or source_file.owner_id != user.id or source_file.deleted_at is not None:
        raise ValidationError("Исходное изображение не найдено или недоступно")
    if source_file.status != FileAsset.Status.READY:
        raise ValidationError("Исходное изображение ещё обрабатывается")
    if source_file.detected_type not in EDIT_IMAGE_TYPES:
        raise ValidationError("Для редактирования поддерживаются PNG, JPEG и WebP")
    if source_file.size_bytes > 8 * 1024 * 1024:
        raise ValidationError("Исходное изображение слишком большое")
    return source_file


def preview(*, model_slug, prompt, size, quality, count):
    model, prompt, count = _validated(model_slug, prompt, size, quality, count)
    unit_price = provider_unit_price(model, size, quality)
    value = require_margin(
        quote_flat(
            provider_cost_native=unit_price * count,
            provider_currency=model.provider_currency,
            base_markup_percent=model.markup_percent,
            provider_slug=model.provider.slug,
            model_slug=model.slug,
            operation_type="images",
        )
    )
    return model, value, prompt, count


def prepare_generation(
    *, user, model_slug, prompt, size, quality, count, idempotency_key,
    confirmed=False, conversation=None, deferred=False, operation="generate", source_file=None,
):
    if conversation is not None and conversation.owner_id != user.id:
        raise ValidationError("Чат не найден или недоступен")
    if operation not in {"generate", "edit"}:
        raise ValidationError("Неподдерживаемая операция с изображением")
    if operation == "edit":
        source_file = validate_edit_source(user=user, source_file=source_file)
    else:
        source_file = None
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValidationError("Корректный Idempotency-Key обязателен")
    existing = ImageGeneration.objects.filter(
        owner=user, idempotency_key=idempotency_key
    ).select_related("model").first()
    if existing:
        return _validate_existing(
            existing, model_slug=model_slug, prompt=prompt, size=size, quality=quality,
            count=count, conversation=conversation, operation=operation, source_file=source_file,
        ), False
    model, value, prompt, count = preview(
        model_slug=model_slug, prompt=prompt, size=size, quality=quality, count=count,
    )
    unit_price = provider_unit_price(model, size, quality)
    if value.user_charge_rub >= Decimal(str(settings.IMAGE_CONFIRM_THRESHOLD_RUB)) and not confirmed:
        raise ValidationError("Подтвердите ожидаемую стоимость генерации")
    snapshot = {
        **value.pricing_snapshot,
        "model_slug": model.slug,
        "provider_slug": model.provider.slug,
        "provider_price_per_image": str(unit_price),
        "price_variant": f"{size}|{quality}",
        "requested_count": count,
        "size": size,
        "quality": quality,
        "conversation_id": str(conversation.id) if conversation else None,
        "operation": operation,
        "source_file_id": str(source_file.id) if source_file else None,
        "source_file_sha256": source_file.sha256 if source_file else None,
    }
    try:
        with transaction.atomic():
            generation = ImageGeneration.objects.create(
                owner=user, conversation=conversation, model=model, prompt=prompt,
                size=size, quality=quality, requested_count=count,
                idempotency_key=idempotency_key, price_snapshot=snapshot,
                estimated_cost_rub=value.user_charge_rub,
                # Provider execution is claimed explicitly after customer reserve is
                # durable. This prevents post_save/bulk-update gaps from bypassing
                # procurement and keeps sync/async generation on one state machine.
                state=ImageGeneration.State.QUEUED,
            )
            reservation = reserve(user, value.user_charge_rub, f"image:{generation.id}")
            generation.reservation = reservation
            generation.save(update_fields=["reservation"])
            return generation, True
    except IntegrityError:
        raced = ImageGeneration.objects.select_related("model").get(
            owner=user, idempotency_key=idempotency_key
        )
        return _validate_existing(
            raced, model_slug=model_slug, prompt=prompt, size=size, quality=quality,
            count=count, conversation=conversation, operation=operation, source_file=source_file,
        ), False


def _ensure_provider_reservation(generation):
    """Reserve exact native image spend before any external provider call."""
    model = generation.model
    if model.adapter_type == ImageModel.AdapterType.ECHO:
        return None
    key = f"image:{generation.id}"
    existing = (
        ProviderSpendReservation.objects.select_related("account")
        .filter(source_key=key, state=ProviderSpendReservation.State.ACTIVE)
        .first()
    )
    if existing is not None:
        return existing

    snapshot = generation.price_snapshot or {}
    try:
        native = Decimal(str(snapshot["provider_price_per_image"])) * Decimal(
            int(generation.requested_count)
        )
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Не удалось определить закупочный резерв изображения") from exc

    try:
        return reserve_provider_spend(
            provider=model.provider,
            amount_native=native,
            source_key=key,
            currency=str(snapshot.get("provider_currency") or model.provider_currency),
        )
    except ValidationError:
        if bool(getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False)):
            raise
        return None


def _claim_queued_generation(generation):
    claimed = ImageGeneration.objects.filter(
        pk=generation.pk, state=ImageGeneration.State.QUEUED
    ).update(state=ImageGeneration.State.RUNNING)
    if claimed:
        generation.state = ImageGeneration.State.RUNNING
        return True
    generation.refresh_from_db(fields=["state", "error_code", "completed_at"])
    return False


def fail_queued_generation(generation, code="queue_unavailable"):
    failed = ImageGeneration.objects.filter(
        pk=generation.pk, state=ImageGeneration.State.QUEUED
    ).update(state=ImageGeneration.State.FAILED, error_code=code, completed_at=timezone.now())
    if failed and generation.reservation_id:
        release(generation.reservation_id)
    generation.refresh_from_db()
    return generation


def _source_for_edit(generation):
    snapshot = generation.price_snapshot or {}
    source_id = snapshot.get("source_file_id")
    if not source_id:
        raise ImageProviderError("Edit source is missing", code="invalid_source_image")
    source = FileAsset.objects.filter(
        pk=source_id,
        owner=generation.owner,
        deleted_at__isnull=True,
        status=FileAsset.Status.READY,
    ).first()
    if source is None or source.sha256 != snapshot.get("source_file_sha256"):
        raise ImageProviderError("Edit source changed or is unavailable", code="invalid_source_image")
    validate_edit_source(user=generation.owner, source_file=source)
    return source


def execute_generation(generation, *, adapter=None, claim_queued=True):
    generation = ImageGeneration.objects.select_related(
        "model", "model__provider", "reservation", "owner"
    ).get(pk=generation.pk)
    if generation.state in {ImageGeneration.State.COMPLETED, ImageGeneration.State.FAILED}:
        return generation
    if claim_queued:
        if not _claim_queued_generation(generation):
            return generation
    elif generation.state != ImageGeneration.State.RUNNING:
        return generation

    model = generation.model
    snapshot = generation.price_snapshot or {}
    try:
        provider_reservation = _ensure_provider_reservation(generation)
        image_adapter = adapter or adapter_for(
            model,
            funding_account_id=(
                provider_reservation.account_id if provider_reservation is not None else None
            ),
        )
        if snapshot.get("operation", "generate") == "edit":
            source = _source_for_edit(generation)
            with source.blob.open("rb") as stream:
                source_bytes = stream.read(8 * 1024 * 1024 + 1)
            if len(source_bytes) > 8 * 1024 * 1024:
                raise ImageProviderError("Edit source is too large", code="invalid_source_image")
            media_type = source.detected_type
            if "/" not in media_type:
                media_type = {"png":"image/png","jpeg":"image/jpeg","jpg":"image/jpeg","webp":"image/webp"}.get(media_type, media_type)
            result = image_adapter.edit(
                model=model.upstream_model,
                prompt=generation.prompt,
                size=generation.size,
                quality=generation.quality,
                count=generation.requested_count,
                image=source_bytes,
                media_type=media_type,
            )
        else:
            result = image_adapter.generate(
                model=model.upstream_model,
                prompt=generation.prompt,
                size=generation.size,
                quality=generation.quality,
                count=generation.requested_count,
            )
        if len(result.images) != generation.requested_count:
            if len(result.images) > generation.requested_count:
                _trip_image_provider(
                    model=model,
                    generation=generation,
                    reason="provider_returned_more_images_than_requested",
                )
            raise ImageProviderError(
                "Provider returned a partial or invalid image count",
                code="image_count_mismatch",
            )
        validated_images = []
        for item in result.images:
            mime = _detect_mime(item.content)
            if mime != item.mime_type or len(item.content) > settings.IMAGE_MAX_RESULT_BYTES:
                raise ImageProviderError("Unsafe image payload", code="invalid_image")
            validate_generated_image(item.content)
            validated_images.append((item, mime))
        for position, (item, mime) in enumerate(validated_images):
            extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
            image = GeneratedImage(
                generation=generation,
                position=position,
                mime_type=mime,
                size_bytes=len(item.content),
                sha256=hashlib.sha256(item.content).hexdigest(),
                revised_prompt=item.revised_prompt,
            )
            image.file.save(f"{position}.{extension}", ContentFile(item.content), save=True)
        actual_count = generation.images.count()
        native = Decimal(snapshot["provider_price_per_image"]) * actual_count
        provider_cost, charge, _profit, _margin = calculate_flat_from_snapshot(native, snapshot)
        if charge > generation.reservation.amount_rub:
            _trip_image_provider(
                model=model, generation=generation,
                reason="image_charge_exceeded_reserved_maximum",
                expected=generation.reservation.amount_rub, actual=charge,
            )
            raise ValidationError("Фактическая стоимость изображения превысила зарезервированный максимум")
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
        if generation.reservation_id:
            release(generation.reservation_id)
        for image in generation.images.all():
            image.file.delete(save=False)
        generation.images.all().delete()
        generation.state = ImageGeneration.State.FAILED
        generation.error_code = exc.code if isinstance(exc, ImageProviderError) else "internal_error"
        generation.completed_at = timezone.now()
        generation.save(update_fields=["state", "error_code", "completed_at"])
        if isinstance(exc, ImageProviderError):
            record_image_quality_failure(model=model, generation=generation, code=exc.code)
    return generation


def generate(
    *, user, model_slug, prompt, size, quality, count, idempotency_key,
    confirmed=False, adapter=None, conversation=None,
):
    generation, created = prepare_generation(
        user=user, model_slug=model_slug, prompt=prompt, size=size, quality=quality,
        count=count, idempotency_key=idempotency_key, confirmed=confirmed,
        conversation=conversation, deferred=False,
    )
    if not created:
        return generation
    return execute_generation(generation, adapter=adapter, claim_queued=True)


def edit(
    *, user, source_file, model_slug, prompt, size, quality, count, idempotency_key,
    confirmed=False, adapter=None, conversation=None,
):
    generation, created = prepare_generation(
        user=user, model_slug=model_slug, prompt=prompt, size=size, quality=quality,
        count=count, idempotency_key=idempotency_key, confirmed=confirmed,
        conversation=conversation, deferred=False, operation="edit", source_file=source_file,
    )
    if not created:
        return generation
    return execute_generation(generation, adapter=adapter, claim_queued=True)
