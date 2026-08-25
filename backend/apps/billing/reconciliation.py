from decimal import Decimal

from django.utils import timezone

from .models import (
    BillingReconciliationItem,
    BillingReconciliationRun,
    CostAnomaly,
    RequestCost,
    Wallet,
)
from .pricing import active_margin_policy, calculate_from_snapshot
from .services import reconstruct, reconstruct_buckets

ZERO = Decimal("0.0000")


def _anomaly(*, kind, dedupe_key, request_cost=None, expected=None, actual=None, details=None):
    return CostAnomaly.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "kind": kind,
            "request_cost": request_cost,
            "expected_rub": expected,
            "actual_rub": actual,
            "details": details or {},
        },
    )[0]


def record_cost_outcome(request_cost, *, model):
    policy = active_margin_policy()
    if (
        request_cost.gross_margin_percent is not None
        and request_cost.gross_margin_percent < policy.minimum_gross_margin_percent
    ):
        _anomaly(
            kind=CostAnomaly.Kind.MARGIN_FLOOR,
            dedupe_key=f"margin:{request_cost.id}",
            request_cost=request_cost,
            expected=policy.minimum_gross_margin_percent,
            actual=request_cost.gross_margin_percent,
            details={"model": model.slug, "provider": model.provider.slug},
        )
    if request_cost.expected_provider_cost_rub is not None:
        ceiling = request_cost.expected_provider_cost_rub * (
            Decimal("1") + policy.anomaly_cost_deviation_percent / Decimal("100")
        )
        if request_cost.provider_cost_rub > ceiling:
            _anomaly(
                kind=CostAnomaly.Kind.COST_DEVIATION,
                dedupe_key=f"cost:{request_cost.id}",
                request_cost=request_cost,
                expected=request_cost.expected_provider_cost_rub,
                actual=request_cost.provider_cost_rub,
                details={
                    "threshold_percent": str(policy.anomaly_cost_deviation_percent),
                    "model": model.slug,
                    "provider": model.provider.slug,
                },
            )


def _wallet_item(run, wallet):
    expected_available, expected_reserved = reconstruct(wallet)
    expected_paid, expected_promo = reconstruct_buckets(wallet)
    mismatch = any(
        [
            wallet.available_rub != expected_available,
            wallet.reserved_rub != expected_reserved,
            wallet.paid_rub != expected_paid,
            wallet.promo_rub != expected_promo,
        ]
    )
    status = (
        BillingReconciliationItem.Status.MANUAL_REVIEW
        if mismatch
        else BillingReconciliationItem.Status.OK
    )
    details = {
        "expected_reserved": str(expected_reserved),
        "actual_reserved": str(wallet.reserved_rub),
        "expected_paid": str(expected_paid),
        "actual_paid": str(wallet.paid_rub),
        "expected_promo": str(expected_promo),
        "actual_promo": str(wallet.promo_rub),
    }
    BillingReconciliationItem.objects.create(
        run=run,
        entity_type="wallet",
        entity_id=str(wallet.id),
        status=status,
        expected_rub=expected_available,
        actual_rub=wallet.available_rub,
        details=details,
    )
    if mismatch:
        _anomaly(
            kind=CostAnomaly.Kind.LEDGER_MISMATCH,
            dedupe_key=f"wallet:{wallet.id}:{run.id}",
            expected=expected_available,
            actual=wallet.available_rub,
            details=details,
        )
    return mismatch


def _request_item(run, request_cost, threshold):
    from apps.chat.models import Generation

    generation = Generation.objects.filter(pk=request_cost.generation_id).first()
    expected_charge = request_cost.charged_rub
    actual_charge = generation.actual_cost_rub if generation else None
    status = BillingReconciliationItem.Status.OK
    details = {}
    if not generation or expected_charge is None or actual_charge is None:
        status = BillingReconciliationItem.Status.MANUAL_REVIEW
    elif request_cost.price_version.model_slug != (generation.routed_model or generation.model):
        status = BillingReconciliationItem.Status.PROVIDER_MISMATCH
    elif abs(expected_charge - actual_charge) > threshold:
        status = (
            BillingReconciliationItem.Status.UNDERCHARGED
            if actual_charge < expected_charge
            else BillingReconciliationItem.Status.OVERCHARGED
        )
    if request_cost.pricing_snapshot and request_cost.charged_rub is not None:
        reproduced = calculate_from_snapshot(
            request_cost.price_version,
            request_cost.input_tokens,
            request_cost.output_tokens,
            request_cost.pricing_snapshot,
        )
        details["reproduced_provider_cost_rub"] = str(reproduced[0])
        details["reproduced_charge_rub"] = str(reproduced[1])
        if (
            abs(reproduced[0] - (request_cost.provider_cost_rub or ZERO)) > threshold
            or abs(reproduced[1] - request_cost.charged_rub) > threshold
        ):
            status = BillingReconciliationItem.Status.PROVIDER_MISMATCH
    BillingReconciliationItem.objects.create(
        run=run,
        entity_type="request_cost",
        entity_id=str(request_cost.id),
        status=status,
        expected_rub=expected_charge,
        actual_rub=actual_charge,
        details=details,
    )
    request_cost.reconciliation_status = status
    request_cost.save(update_fields=["reconciliation_status"])
    if status != BillingReconciliationItem.Status.OK:
        _anomaly(
            kind=(
                CostAnomaly.Kind.PROVIDER_MISMATCH
                if status == BillingReconciliationItem.Status.PROVIDER_MISMATCH
                else CostAnomaly.Kind.REQUEST_MISMATCH
            ),
            dedupe_key=f"request:{request_cost.id}:{status}",
            request_cost=request_cost,
            expected=expected_charge,
            actual=actual_charge,
            details=details,
        )
    return status != BillingReconciliationItem.Status.OK


def reconcile_billing():
    run = BillingReconciliationRun.objects.create()
    try:
        policy = active_margin_policy()
        for wallet in Wallet.objects.all().iterator():
            run.checked_wallets += 1
            run.discrepancy_count += int(_wallet_item(run, wallet))
        requests = RequestCost.objects.select_related("price_version").filter(
            charged_rub__isnull=False
        )
        for request_cost in requests.iterator():
            run.checked_requests += 1
            run.discrepancy_count += int(
                _request_item(run, request_cost, policy.reconciliation_threshold_rub)
            )
        run.status = BillingReconciliationRun.Status.SUCCEEDED
        run.summary = {
            "wallets": run.checked_wallets,
            "requests": run.checked_requests,
            "discrepancies": run.discrepancy_count,
        }
    except Exception:
        run.status = BillingReconciliationRun.Status.FAILED
        raise
    finally:
        run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "checked_wallets",
                "checked_requests",
                "discrepancy_count",
                "summary",
                "finished_at",
            ]
        )
    return run
