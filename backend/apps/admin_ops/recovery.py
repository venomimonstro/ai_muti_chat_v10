from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.b2b_api.models import APIUsage
from apps.billing.models import BalanceReservation, RequestCost
from apps.billing.services import release
from apps.chat.models import CompareRun, CompareVariant, Generation, GenerationAttempt, Message
from apps.chat.paid_search_billing import recover_search_reservations
from apps.chat.partial_billing import settle_delivered_partial
from apps.files.models import FileAsset, FileProcessingJob
from apps.image_studio.models import ImageGeneration
from apps.procurement.models import ProviderSpendReservation
from apps.procurement.services import release_provider_spend

ZERO = Decimal("0.0000")


def _cutoff():
    return timezone.now() - timedelta(seconds=settings.OPERATION_STALE_TIMEOUT_SECONDS)


def _generation_cutoff():
    return timezone.now() - timedelta(
        seconds=max(
            60,
            int(
                getattr(
                    settings,
                    "CHAT_GENERATION_STALE_TIMEOUT_SECONDS",
                    settings.OPERATION_STALE_TIMEOUT_SECONDS,
                )
            ),
        )
    )


def _release_provider_prefix(prefix):
    ids = ProviderSpendReservation.objects.filter(
        source_key__startswith=prefix,
        state=ProviderSpendReservation.State.ACTIVE,
    ).values_list("id", flat=True)
    for reservation_id in list(ids):
        release_provider_spend(reservation_id)


@transaction.atomic
def _recover_generation(pk):
    generation = (
        Generation.objects.select_for_update().select_related("assistant_message").get(pk=pk)
    )
    cutoff = _generation_cutoff()
    if generation.state not in {
        Generation.State.QUEUED,
        Generation.State.RUNNING,
    } or generation.created_at >= cutoff:
        return False

    # A multi-provider request can legitimately outlive the generation-level stale
    # timeout while a *new* fallback attempt has just started. Never kill that fresh
    # provider call merely because the original Generation row is old. Attempts are
    # immutable enough to serve as the authoritative activity marker for this flow.
    if GenerationAttempt.objects.filter(
        generation=generation,
        state=GenerationAttempt.State.RUNNING,
        started_at__gte=cutoff,
    ).exists():
        return False

    assistant = generation.assistant_message
    request_cost = (
        RequestCost.objects.select_for_update()
        .filter(generation_id=generation.id)
        .first()
    )
    provider_usage_confirmed = bool(
        request_cost is not None
        and request_cost.provider_cost_rub is not None
        and (request_cost.input_tokens or request_cost.output_tokens)
    )

    if provider_usage_confirmed:
        # A process can die after authoritative provider usage has been persisted
        # but before the normal stream terminal commit. Reuse the exact same
        # customer settlement path as stream cancellation: charge only confirmed
        # usage, cap it by the pre-authorized reserve and return the remainder.
        charge = settle_delivered_partial(generation, assistant.content)
    else:
        closed = release(generation.reservation_id) if generation.reservation_id else None
        charge = closed.actual_rub if closed and closed.actual_rub is not None else ZERO
        # No authoritative provider usage exists, therefore no provider spend may
        # remain reserved for the abandoned request.
        if request_cost is not None:
            _release_provider_prefix(f"chat:{request_cost.id}:")

    assistant.status = Message.Status.PARTIAL if assistant.content else Message.Status.FAILED
    assistant.save(update_fields=["status"])
    generation.state = Generation.State.FAILED
    generation.error_code = "stale_operation_recovered"
    generation.completed_at = timezone.now()
    generation.actual_cost_rub = charge
    update_fields = ["state", "error_code", "completed_at", "actual_cost_rub"]
    if request_cost is not None and (
        request_cost.input_tokens or request_cost.output_tokens
    ):
        generation.input_tokens = request_cost.input_tokens
        generation.output_tokens = request_cost.output_tokens
        update_fields.extend(["input_tokens", "output_tokens"])
    generation.save(update_fields=update_fields)
    return True


@transaction.atomic
def _recover_compare(pk):
    run = CompareRun.objects.select_for_update().get(pk=pk)
    if run.state != CompareRun.State.RUNNING or run.created_at >= _cutoff():
        return False
    if run.reservation_id:
        release(run.reservation_id)
    now = timezone.now()
    stale_variants = list(
        run.variants.filter(
            state__in=[CompareVariant.State.QUEUED, CompareVariant.State.RUNNING]
        ).values_list("id", flat=True)
    )
    run.variants.filter(pk__in=stale_variants).update(
        state=CompareVariant.State.FAILED,
        error_code="stale_operation_recovered",
        completed_at=now,
    )
    # Bulk update does not fire post_save, therefore procurement reservations
    # must be released explicitly for variants that never reached completion.
    for variant_id in stale_variants:
        _release_provider_prefix(f"compare:{variant_id}")
    completed = run.variants.filter(state=CompareVariant.State.COMPLETED).exists()
    run.state = CompareRun.State.PARTIAL if completed else CompareRun.State.FAILED
    run.completed_at = now
    run.save(update_fields=["state", "completed_at"])
    return True


@transaction.atomic
def _recover_compare_synthesis(pk):
    run = CompareRun.objects.select_for_update().get(pk=pk)
    if not run.synthesis_reservation_id or run.synthesis_output:
        return False
    reservation = BalanceReservation.objects.select_for_update().filter(
        pk=run.synthesis_reservation_id,
        state=BalanceReservation.State.ACTIVE,
    ).first()
    if reservation is None or reservation.created_at >= _cutoff():
        return False
    release(reservation.id)
    run.synthesis_reservation_id = None
    run.save(update_fields=["synthesis_reservation_id"])
    return True


@transaction.atomic
def _recover_image(pk):
    generation = ImageGeneration.objects.select_for_update().get(pk=pk)
    if generation.state not in {
        ImageGeneration.State.QUEUED,
        ImageGeneration.State.RUNNING,
    } or generation.created_at >= _cutoff():
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


def recover_terminal_chat_reservations(*, older_than_seconds=None, limit=500):
    """Release customer reserves left ACTIVE after a terminal chat failure.

    Normal failure paths release synchronously. This sweep is the durable safety net
    for a database/process failure during that refund. It handles only FAILED or
    CANCELLED Generations; COMPLETED reservations require settlement, never refund.
    """
    age = (
        settings.OPERATION_STALE_TIMEOUT_SECONDS
        if older_than_seconds is None
        else max(0, int(older_than_seconds))
    )
    cutoff = timezone.now() - timedelta(seconds=age)
    rows = list(
        BalanceReservation.objects.filter(
            state=BalanceReservation.State.ACTIVE,
            idempotency_key__startswith="generation:",
            created_at__lt=cutoff,
        )
        .order_by("created_at")[: max(1, min(int(limit), 2000))]
    )
    released = 0
    deferred = 0
    for reservation in rows:
        generation_id = str(reservation.idempotency_key or "").split("generation:", 1)[-1]
        generation = Generation.objects.filter(pk=generation_id).only("state").first()
        if generation is None:
            deferred += 1
            continue
        if generation.state not in {Generation.State.FAILED, Generation.State.CANCELLED}:
            deferred += 1
            continue
        try:
            release(reservation.id)
            released += 1
        except Exception:
            deferred += 1
    return {"checked": len(rows), "released": released, "deferred": deferred}

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
    if asset.status not in {
        FileAsset.Status.UPLOADED,
        FileAsset.Status.QUARANTINE,
        FileAsset.Status.PARSING,
    } or asset.updated_at >= _cutoff():
        return False
    now = timezone.now()
    asset.status = FileAsset.Status.FAILED
    asset.error_code = "stale_operation_recovered"
    asset.save(update_fields=["status", "error_code", "updated_at"])
    asset.jobs.filter(
        state__in=[FileProcessingJob.State.QUEUED, FileProcessingJob.State.RUNNING]
    ).update(
        state=FileProcessingJob.State.FAILED,
        error_code="stale_operation_recovered",
        finished_at=now,
    )
    return True


def recover_stale_operations():
    cutoff = _cutoff()
    generation_cutoff = _generation_cutoff()
    groups = (
        (
            "generations",
            Generation.objects.filter(
                state__in=[Generation.State.QUEUED, Generation.State.RUNNING],
                created_at__lt=generation_cutoff,
            ),
            _recover_generation,
        ),
        (
            "compare_runs",
            CompareRun.objects.filter(state=CompareRun.State.RUNNING, created_at__lt=cutoff),
            _recover_compare,
        ),
        (
            "compare_synthesis",
            CompareRun.objects.filter(
                synthesis_reservation_id__isnull=False,
                synthesis_output="",
                synthesis_reservation_id__in=BalanceReservation.objects.filter(
                    state=BalanceReservation.State.ACTIVE,
                    created_at__lt=cutoff,
                ).values("id"),
            ),
            _recover_compare_synthesis,
        ),
        (
            "image_generations",
            ImageGeneration.objects.filter(
                state__in=[ImageGeneration.State.QUEUED, ImageGeneration.State.RUNNING],
                created_at__lt=cutoff,
            ),
            _recover_image,
        ),
        (
            "files",
            FileAsset.objects.filter(
                status__in=[
                    FileAsset.Status.UPLOADED,
                    FileAsset.Status.QUARANTINE,
                    FileAsset.Status.PARSING,
                ],
                updated_at__lt=cutoff,
            ),
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
    result["terminal_chat_reservations"] = recover_terminal_chat_reservations(
        older_than_seconds=settings.OPERATION_STALE_TIMEOUT_SECONDS
    )
    result["web_search_reservations"] = recover_search_reservations(
        older_than_seconds=settings.OPERATION_STALE_TIMEOUT_SECONDS
    )
    return result