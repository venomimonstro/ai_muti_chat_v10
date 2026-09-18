from celery import shared_task

from .models import ImageGeneration
from .services import execute_generation


@shared_task(bind=True, autoretry_for=(), max_retries=0)
def execute_image_generation_task(self, generation_id):
    generation = ImageGeneration.objects.filter(pk=generation_id).first()
    if generation is None:
        return {"processed": False, "reason": "missing"}
    result = execute_generation(generation, claim_queued=True)
    return {
        "processed": result.state in {ImageGeneration.State.COMPLETED, ImageGeneration.State.FAILED},
        "state": result.state,
        "error_code": result.error_code,
    }
