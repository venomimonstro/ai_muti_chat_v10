from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.image_studio.models import ImageGeneration, ImageModel
from apps.image_studio.services import execute_generation, prepare_generation, preview


def _select_image_model():
    model = (
        ImageModel.objects.select_related("provider")
        .filter(
            enabled=True,
            provider__enabled=True,
            provider__emergency_disabled=False,
        )
        .order_by("display_name")
        .first()
    )
    if model is None:
        raise ValidationError("В Image Studio нет доступной image-модели")
    return model


def _request_spec(node, prompt: str):
    model = _select_image_model()
    size = str(node.get("size") or (model.supported_sizes or ["1024x1024"])[0])
    quality = str(node.get("quality") or (model.supported_qualities or ["standard"])[0])
    image_prompt = str(node.get("prompt") or prompt or "").strip()
    if not image_prompt:
        raise ValidationError("Для генерации изображения нужен промпт")
    _model, value, normalized_prompt, _count = preview(
        model_slug=model.slug,
        prompt=image_prompt,
        size=size,
        quality=quality,
        count=1,
    )
    return model, size, quality, normalized_prompt, Decimal(value.user_charge_rub)


def generate_agent_image(*, run, node, prompt: str, max_cost_rub=None) -> dict:
    model, size, quality, image_prompt, estimated_cost = _request_spec(node, prompt or run.objective)
    if max_cost_rub is not None and estimated_cost > Decimal(str(max_cost_rub)):
        raise ValidationError(
            f"Расчётная стоимость изображения {estimated_cost} ₽ превышает оставшийся лимит {max_cost_rub} ₽"
        )

    node_id = str(node.get("id") or "image")
    generation, created = prepare_generation(
        user=run.owner,
        model_slug=model.slug,
        prompt=image_prompt,
        size=size,
        quality=quality,
        count=1,
        idempotency_key=f"agent-image:{run.id}:{node_id}",
        confirmed=True,
        conversation=None,
        deferred=False,
    )
    if created or generation.state == ImageGeneration.State.RUNNING:
        generation = execute_generation(generation, claim_queued=False)
    else:
        generation.refresh_from_db()

    if generation.state != ImageGeneration.State.COMPLETED:
        raise ValidationError(
            f"Image Studio не завершил генерацию: {generation.error_code or generation.state}"
        )

    images = list(generation.images.all())
    actual_cost = Decimal(generation.actual_cost_rub or generation.estimated_cost_rub or 0)
    return {
        "generation_id": str(generation.id),
        "model": model.display_name,
        "count": len(images),
        "estimated_cost_rub": str(estimated_cost),
        "actual_cost_rub": str(actual_cost),
        "images": [
            {
                "id": str(image.id),
                "mime_type": image.mime_type,
                "size_bytes": image.size_bytes,
            }
            for image in images
        ],
    }
