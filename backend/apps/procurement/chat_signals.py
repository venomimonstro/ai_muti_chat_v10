"""Fail-safe cleanup for chat procurement reservations.

A failed/cancelled generation that never received authoritative provider usage must
release its active provider reservation. Once provider usage/cost is persisted we
must *not* release: that state belongs to financial reconciliation because the
external provider may already have charged us.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.billing.models import RequestCost
from apps.chat.models import Generation

from .models import ProviderSpend, ProviderSpendReservation
from .services import release_provider_spend

logger = logging.getLogger(__name__)


def release_stale_terminal_chat_reservations(*, limit: int = 500) -> int:
    """Release orphaned chat procurement reservations left by older runtimes.

    Only terminal generations with no authoritative provider cost/spend are safe to
    release. This makes the repair idempotent and prevents stale reservations from
    silently removing otherwise healthy models from customer routing.
    """
    released = 0
    active = list(
        ProviderSpendReservation.objects.filter(
            source_key__startswith="chat:",
            state=ProviderSpendReservation.State.ACTIVE,
        )
        .order_by("created_at")[: max(1, int(limit))]
    )
    for reservation in active:
        parts = str(reservation.source_key or "").split(":", 2)
        if len(parts) < 3:
            continue
        request_cost = RequestCost.objects.filter(pk=parts[1]).first()
        if request_cost is None:
            continue
        if request_cost.provider_cost_rub is not None:
            continue
        if ProviderSpend.objects.filter(
            source_type="chat", source_id=str(request_cost.id)
        ).exists():
            continue
        terminal = Generation.objects.filter(
            pk=request_cost.generation_id,
            state__in=[Generation.State.FAILED, Generation.State.CANCELLED],
        ).exists()
        if not terminal:
            continue
        try:
            release_provider_spend(reservation.id)
            released += 1
        except Exception:
            logger.exception(
                "Failed to repair stale chat provider reservation request_cost=%s reservation=%s",
                request_cost.id,
                reservation.id,
            )
    return released


@receiver(post_save, sender=Generation)
def release_terminal_chat_procurement(sender, instance, **kwargs):
    if instance.state not in {Generation.State.FAILED, Generation.State.CANCELLED}:
        return

    request_cost = RequestCost.objects.filter(generation_id=instance.id).first()
    if request_cost is None:
        return

    # Provider usage/cost already exists: never pretend the external spend did not
    # happen. Existing/recoverable settlement must remain available to reconciliation.
    if request_cost.provider_cost_rub is not None:
        return
    if ProviderSpend.objects.filter(source_type="chat", source_id=str(request_cost.id)).exists():
        return

    active = ProviderSpendReservation.objects.filter(
        source_key__startswith=f"chat:{request_cost.id}:",
        state=ProviderSpendReservation.State.ACTIVE,
    ).values_list("id", flat=True)
    for reservation_id in list(active):
        try:
            release_provider_spend(reservation_id)
        except Exception:
            logger.exception(
                "Failed to release orphaned chat provider reservation generation=%s reservation=%s",
                instance.id,
                reservation_id,
            )
