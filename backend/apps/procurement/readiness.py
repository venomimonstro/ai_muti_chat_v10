from decimal import Decimal, InvalidOperation, ROUND_UP

from django.conf import settings

from apps.ai_registry.models import Provider

from .services import account_available_native, credential_is_configured, default_account

NATIVE_STEP = Decimal("0.000001")
SPECIAL_EXTERNAL_PROVIDER_SLUGS = {"gigachat", "openrouter"}


def _strict_runtime() -> bool:
    return bool(getattr(settings, "PROCUREMENT_RUNTIME_FAIL_CLOSED", False))


def _test_echo(provider: Provider) -> bool:
    return (
        provider.adapter_type == Provider.AdapterType.ECHO
        and provider.slug not in SPECIAL_EXTERNAL_PROVIDER_SLUGS
    )


def quote_has_procurement_capacity(provider: Provider, price_quote) -> bool:
    """Return whether the purchased provider balance can fund this exact quote.

    Provider availability alone only proves that some balance exists. Commercial
    routing needs a stronger request-level check before selecting a model so an
    almost-empty account cannot win routing and fail later while RequestCost is
    reserving provider spend.
    """
    if _test_echo(provider):
        return True

    account = default_account(provider)
    if account is None:
        return not _strict_runtime()
    if not credential_is_configured(account):
        return False

    snapshot = getattr(price_quote, "pricing_snapshot", {}) or {}
    quote_currency = str(snapshot.get("provider_currency") or "").upper().strip()
    account_currency = str(account.currency or "").upper().strip()
    if not quote_currency or not account_currency or quote_currency != account_currency:
        # The quote FX belongs to provider_currency. Reusing it for a funding
        # account in another currency would corrupt the procurement reservation.
        return False

    try:
        fx_rate = Decimal(str(snapshot.get("fx_rate") or "0"))
        provider_cost_rub = Decimal(str(getattr(price_quote, "provider_cost_rub", 0) or 0))
    except (InvalidOperation, TypeError, ValueError):
        return False
    if fx_rate <= 0:
        return False
    if provider_cost_rub <= 0:
        return True

    required_native = (provider_cost_rub / fx_rate).quantize(NATIVE_STEP, rounding=ROUND_UP)
    if required_native <= 0:
        return True
    return account_available_native(account) >= required_native
