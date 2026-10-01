from __future__ import annotations

import logging

from apps.billing.models import BalanceReservation
from apps.billing.services import release

logger = logging.getLogger(__name__)
REFUND_REASON = "search_context_not_delivered"


def _undelivered_marker(generation) -> bool:
    snapshot = generation.context_snapshot if isinstance(generation.context_snapshot, dict) else {}
    web_state = snapshot.get("web_search") if isinstance(snapshot, dict) else None
    return bool(
        isinstance(web_state, dict)
        and web_state.get("customer_refunded_reason") == REFUND_REASON
    )


def install(*, streaming_module, paid_search_module) -> None:
    """Do not charge the customer for paid search that never reaches the model.

    The upstream search provider can successfully return results while context-budget
    enforcement later decides that none of those results can be inserted into the LLM
    request. Procurement spend is already real and remains recorded, but the customer
    must not be charged for a tool whose data was not delivered into the answer path.

    The semantic refund marker is persisted in Generation.context_snapshot, so a
    transient failure while releasing the customer reservation cannot later turn into
    an incorrect terminal charge when the answer itself completes.
    """
    raw_enrich = streaming_module.enrich_snapshot_with_web
    if getattr(raw_enrich, "_ai_workspace_search_delivery_billing", False):
        return
    raw_finish = paid_search_module._finish_customer_search_charge

    def enrich(snapshot: dict, query: str, *, required: bool):
        result = raw_enrich(snapshot, query, required=required)
        usage = dict(paid_search_module._usage.get() or {})
        reservation_id = str(usage.get("customer_reservation_id") or "").strip()
        web_state = result.get("web_search") if isinstance(result, dict) else None
        delivered = bool(isinstance(web_state, dict) and web_state.get("used"))
        if reservation_id and not delivered:
            # Persist semantic intent into the context even if the immediate DB
            # release fails. Terminal billing will inspect the same marker and retry
            # release rather than settle the undelivered tool charge.
            result["web_search"] = {
                **(web_state or {}),
                "customer_charge_rub": "0",
                "customer_refunded_reason": REFUND_REASON,
            }
            usage["customer_charge_rub"] = "0"
            usage["customer_refunded_reason"] = REFUND_REASON
            try:
                release(reservation_id)
            except Exception:
                logger.exception(
                    "Failed to release undelivered paid-search reservation id=%s",
                    reservation_id,
                )
            else:
                usage.pop("customer_reservation_id", None)
            paid_search_module._usage.set(usage)
        return result

    def finish_customer_search_charge(generation):
        if not _undelivered_marker(generation):
            return raw_finish(generation)
        reservation = BalanceReservation.objects.filter(
            idempotency_key=f"web-search:{generation.id}"
        ).first()
        if reservation is None or reservation.state != BalanceReservation.State.ACTIVE:
            return reservation
        return release(reservation.id)

    enrich._ai_workspace_search_delivery_billing = True
    enrich._raw_enrich = raw_enrich
    finish_customer_search_charge._ai_workspace_search_delivery_billing = True
    finish_customer_search_charge._raw_finish = raw_finish
    streaming_module.enrich_snapshot_with_web = enrich
    paid_search_module._finish_customer_search_charge = finish_customer_search_charge
