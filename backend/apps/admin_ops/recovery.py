from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.b2b_api.models import APIUsage
from apps.billing.services import release
from apps.chat.models import CompareRun, CompareVariant, Generation, Message
from apps.files.models import FileAsset, FileProcessingJob
from apps.image_studio.models import ImageGeneration


def _cutoff():
    return timezone.now() - timedelta(seconds=settings.OPERATION_STALE_TIMEOUT_SECONDS)


@transaction.atomic
def _recover_generation(pk):
    generation = (
        Generation.objects.select_for_update().select_related("assistant_message").get(pk=pk)
    )
    if generation.state not in {
        Generation.State.QUEUED,
        Generation.State.RUNNING,
    } or generation.created_at >= _cutoff():
        return False
    if generation.reservation_id:
        release(generation.reservation_id)
    assistant = generation.assistant_message
    assistant.status = Message.Status.PARTIAL if assistant.content else Message.Status.FAILED
    assistant.save(update_fields=["status"])
    generation.state = Generation.State.FAILED
    generation.error_code = "stale_operation_recovered"
    generation.completed_at = timezone.now()
    generation.save(update_fields=["state", "error_code", "completed_at"])
    return True


@transaction.atomic
def _recover_compare(pk):
    run = CompareRun.objects.select_for_update().get(pk=pk)
    if run.state != CompareRun.State.RUNNING or run.created_at >= _cutoff():
        return False
    if run.reservation_id:
        release(run.reservation_id)
    now = timezone.now()
    run.variants.filter(
        state__in=[CompareVariant.State.QUEUED, CompareVariant.State.RUNNING]
    ).update(
        state=CompareVariant.State.FAILED,
        error_code="stale_operation_recovered",
        completed_at=now,
    )
    completed = run.variants.filter(state=CompareVariant.State.COMPLETED).exists()
    run.state = CompareRun.State.PARTIAL if completed else CompareRun.State.FAILED
    run.completed_at = now
    run.save(update_fields=["state", "completed_at"])
    return True


@transaction.atomic
def _recover_image(pk):
    generation = ImageGeneration.objects.select_for_update().get(pk=pk)
    if generation.state != ImageGeneration.State.RUNNING or generation.created_at >= _cutoff():
        return False
    if generation.reservation_id:
        release(generation.reservation_id)
    for image in generation.images.all():
        image.file.delete(save=False)
    generation.images.all().delete()
    generation.state = ImageGeneration.State.FAILED
    generation.error_code = "stale_operation_recovered"
    generation.completed_at = timezone.now()
    generation.save(update_fields=["state", "error_code", "completed_at"])
    return True


@transaction.atomic
def _recover_api_usage(pk):
    usage = APIUsage.objects.select_for_update().get(pk=pk)
    cutoff = timezone.now() - timedelta(seconds=settings.B2B_API_RUNNING_TIMEOUT_SECONDS)
    if usage.state != APIUsage.State.RUNNING or usage.created_at >= cutoff:
        return False
    if usage.reservation_id:
        release(usage.reservation_id)
    usage.state = APIUsage.State.FAILED
    usage.error_code = "stale_operation_recovered"
    usage.completed_at = timezone.now()
    usage.save(update_fields=["state", "error_code", "completed_at"])
    return True


def recover_stale_api_usages(*, api_key=None):
    cutoff = timezone.now() - timedelta(seconds=settings.B2B_API_RUNNING_TIMEOUT_SECONDS)
    queryset = APIUsage.objects.filter(state=APIUsage.State.RUNNING, created_at__lt=cutoff)
    if api_key is not None:
        queryset = queryset.filter(api_key=api_key)
    count = 0
    for pk in queryset.values_list("pk", flat=True).iterator():
        try:
            count += int(_recover_api_usage(pk))
        except APIUsage.DoesNotExist:
            continue
    return count


@transaction.atomic
def _recover_file(pk):
    asset = FileAsset.objects.select_for_update().get(pk=pk)
    if asset.status != FileAsset.Status.PARSING or asset.updated_at >= _cutoff():
        return False
    now = timezone.now()
    asset.status = FileAsset.Status.FAILED
    asset.error_code = "stale_operation_recovered"
    asset.save(update_fields=["status", "error_code", "updated_at"])
    asset.jobs.filter(state=FileProcessingJob.State.RUNNING).update(
        state=FileProcessingJob.State.FAILED,
        error_code="stale_operation_recovered",
        finished_at=now,
    )
    return True


def recover_stale_operations():
    cutoff = _cutoff()
    groups = (
        (
            "generations",
            Generation.objects.filter(
                state__in=[Generation.State.QUEUED, Generation.State.RUNNING],
                created_at__lt=cutoff,
            ),
            _recover_generation,
        ),
        (
            "compare_runs",
            CompareRun.objects.filter(state=CompareRun.State.RUNNING, created_at__lt=cutoff),
            _recover_compare,
        ),
        (
            "image_generations",
            ImageGeneration.objects.filter(
                state=ImageGeneration.State.RUNNING, created_at__lt=cutoff
            ),
            _recover_image,
        ),
        (
            "files",
            FileAsset.objects.filter(status=FileAsset.Status.PARSING, updated_at__lt=cutoff),
            _recover_file,
        ),
    )
    result = {}
    for name, queryset, recover in groups:
        count = 0
        for pk in queryset.values_list("pk", flat=True).iterator():
            try:
                count += int(recover(pk))
            except queryset.model.DoesNotExist:
                continue
        result[name] = count
    result["api_usages"] = recover_stale_api_usages()
    return result
