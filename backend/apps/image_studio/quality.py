import os
from datetime import timedelta

from django.utils import timezone

from apps.billing.models import CostAnomaly

QUALITY_CODES = {"blank_image", "invalid_image", "invalid_response"}


def record_image_quality_failure(*, model, generation, code):
    if code not in QUALITY_CODES:
        return False
    CostAnomaly.objects.get_or_create(
        dedupe_key=f"image-quality:{generation.id}:{code}",
        defaults={
            "kind": CostAnomaly.Kind.PROVIDER_MISMATCH,
            "severity": "warning",
            "model_slug": model.slug,
            "provider_slug": model.provider.slug,
            "details": {
                "reason": "invalid_generated_image",
                "code": code,
                "generation_id": str(generation.id),
            },
        },
    )
    threshold = max(1, int(os.getenv("IMAGE_PROVIDER_QUALITY_FAILURE_THRESHOLD", "3")))
    window_minutes = max(5, int(os.getenv("IMAGE_PROVIDER_QUALITY_WINDOW_MINUTES", "60")))
    since = timezone.now() - timedelta(minutes=window_minutes)
    recent = CostAnomaly.objects.filter(
        model_slug=model.slug,
        provider_slug=model.provider.slug,
        dedupe_key__startswith="image-quality:",
        created_at__gte=since,
    ).count()
    if recent < threshold:
        return False
    model.__class__.objects.filter(pk=model.pk).update(enabled=False)
    model.enabled = False
    CostAnomaly.objects.get_or_create(
        dedupe_key=f"image-quality-circuit:{model.slug}:{timezone.now().strftime('%Y%m%d%H')}",
        defaults={
            "kind": CostAnomaly.Kind.PROVIDER_MISMATCH,
            "severity": "critical",
            "model_slug": model.slug,
            "provider_slug": model.provider.slug,
            "details": {
                "reason": "image_model_disabled_after_repeated_invalid_results",
                "recent_failures": recent,
                "threshold": threshold,
                "window_minutes": window_minutes,
            },
        },
    )
    return True
