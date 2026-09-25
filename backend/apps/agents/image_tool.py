from django.core.exceptions import ValidationError

from apps.image_studio.models import ImageGeneration, ImageModel
from apps.image_studio.services import execute_generation, prepare_generation


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


def generate_agent_image(*, run, node, prompt: str) -> dict:
    model = _select_image_model()
    node_id = str(node.get("id") or "image")
    size = str(node.get("size") or (model.supported_sizes or ["1024x1024"])[0])
    quality = str(node.get("quality") or (model.supported_qualities or ["standard"])[0])
    image_prompt = str(node.get("prompt") or prompt or run.objective).strip()
    if not image_prompt:
        raise ValidationError("Для генерации изображения нужен промпт")

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
    return {
        "generation_id": str(generation.id),
        "model": model.display_name,
        "count": len(images),
        "actual_cost_rub": str(generation.actual_cost_rub or generation.estimated_cost_rub or 0),
        "images": [
            {
                "id": str(image.id),
                "mime_type": image.mime_type,
                "size_bytes": image.size_bytes,
            }
            for image in images
        ],
    }
