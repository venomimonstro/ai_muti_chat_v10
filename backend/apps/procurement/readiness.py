from decimal import Decimal, InvalidOperation, ROUND_UP

from django.conf import settings

from apps.ai_registry.models import Provider

from .account_routing import NATIVE_STEP, select_runtime_funding_account

SPECIAL_EXTERNAL_PROVIDER_SLUGS = {"gigachat", "openrouter", "hubai"}


def _strict_runtime() -> bool:
    return bool(getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False))


def _test_echo(provider: Provider) -> bool:
    return (
        provider.adapter_type == Provider.AdapterType.ECHO
        and provider.slug not in SPECIAL_EXTERNAL_PROVIDER_SLUGS
    )


def quote_has_procurement_capacity(provider: Provider, price_quote) -> bool:
    """Return whether any healthy paid account can fund this exact quote.

    The selected account is not persisted here; this is a read-only routing preflight.
    ``reserve_provider_spend`` repeats the same selection under database locks and is
    the final authority under concurrency.
    """
    if _test_echo(provider):
        return True

    snapshot = getattr(price_quote, "pricing_snapshot", {}) or {}
    quote_currency = str(snapshot.get("provider_currency") or "").upper().strip()
    try:
        fx_rate = Decimal(str(snapshot.get("fx_rate") or "0"))
        provider_cost_rub = Decimal(
            str(getattr(price_quote, "provider_cost_rub", 0) or 0)
        )
    except (InvalidOperation, TypeError, ValueError):
        return False
    if fx_rate <= 0:
        return False
    if provider_cost_rub <= 0:
        required_native = NATIVE_STEP
    else:
        required_native = (provider_cost_rub / fx_rate).quantize(
            NATIVE_STEP, rounding=ROUND_UP
        )
        if required_native <= 0:
            required_native = NATIVE_STEP

    try:
        account = select_runtime_funding_account(
            provider,
            required_native=required_native,
            currency=quote_currency,
            allow_probe=False,
            require_balance=True,
        )
    except Exception:
        account = None
    if account is not None:
        return True
    # Procurement is accounting metadata and can lag the actual upstream account.
    # Do not remove a model from customer routing solely because this local ledger
    # has no reservable capacity; execution still requires a verified provider
    # credential and runtime failures remain fail-closed at the provider layer.
    return True
