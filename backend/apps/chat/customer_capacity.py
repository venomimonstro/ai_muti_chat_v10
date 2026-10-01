from __future__ import annotations

import sys
from contextvars import ContextVar
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.accounts.services import enforce_spend_limits
from apps.billing.models import Wallet


_current_user: ContextVar[object | None] = ContextVar(
    "chat_customer_capacity_user",
    default=None,
)
_customer_rejections: ContextVar[int] = ContextVar(
    "chat_customer_capacity_rejections",
    default=0,
)


def _charge(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def customer_can_reserve(user, amount) -> bool:
    """Best-effort preflight capacity check; final reserve remains authoritative.

    This never mutates the wallet. It prevents an expensive fallback candidate from
    making a cheaper, otherwise valid primary route unusable. A balance race after
    this check is still closed safely by the transactional billing ``reserve()``.
    """
    charge = _charge(amount)
    if user is None or charge <= 0:
        return False
    wallet, _ = Wallet.objects.get_or_create(user=user)
    if wallet.available_rub < charge:
        return False
    try:
        enforce_spend_limits(wallet, charge)
    except ValidationError:
        return False
    # Unexpected DB/cache/runtime errors deliberately propagate. Misreporting an
    # infrastructure failure as "insufficient balance" makes incidents much harder
    # to diagnose and can hide a platform-wide outage from the diagnostics center.
    return True


def _mark_customer_rejection() -> None:
    _customer_rejections.set(_customer_rejections.get() + 1)


def _friendly_capacity_error(exc):
    message = str(exc)
    if _customer_rejections.get() <= 0:
        return exc
    if "доступным api-балансом" not in message.casefold():
        return exc
    return ValidationError(
        "Сейчас на балансе недостаточно средств для доступных AI-маршрутов. "
        "Пополните баланс или выберите более экономный уровень."
    )


def install(*, streaming_module, cost_preview_module) -> None:
    """Apply one customer-funding eligibility rule to preview and real prepare."""
    raw_stream_capacity = streaming_module.quote_has_procurement_capacity
    if not getattr(raw_stream_capacity, "_ai_workspace_customer_capacity", False):

        def stream_capacity(provider, value):
            if not raw_stream_capacity(provider, value):
                return False
            user = _current_user.get()
            if user is None:
                return True
            ready = customer_can_reserve(user, getattr(value, "user_charge_rub", 0))
            if not ready:
                _mark_customer_rejection()
            return ready

        stream_capacity._ai_workspace_customer_capacity = True
        stream_capacity._raw_capacity = raw_stream_capacity
        streaming_module.quote_has_procurement_capacity = stream_capacity

    raw_preview_capacity = cost_preview_module.quote_has_procurement_capacity
    if not getattr(raw_preview_capacity, "_ai_workspace_customer_capacity", False):

        def preview_capacity(provider, value):
            if not raw_preview_capacity(provider, value):
                return False
            user = _current_user.get()
            if user is None:
                return True
            ready = customer_can_reserve(user, getattr(value, "user_charge_rub", 0))
            if not ready:
                _mark_customer_rejection()
            return ready

        preview_capacity._ai_workspace_customer_capacity = True
        preview_capacity._raw_capacity = raw_preview_capacity
        cost_preview_module.quote_has_procurement_capacity = preview_capacity

    raw_prepare = streaming_module.prepare
    if not getattr(raw_prepare, "_ai_workspace_customer_capacity", False):

        def prepare(*args, **kwargs):
            token_user = _current_user.set(kwargs.get("user"))
            token_rejections = _customer_rejections.set(0)
            try:
                return raw_prepare(*args, **kwargs)
            except ValidationError as exc:
                raise _friendly_capacity_error(exc) from exc
            finally:
                _customer_rejections.reset(token_rejections)
                _current_user.reset(token_user)

        prepare._ai_workspace_customer_capacity = True
        prepare._raw_prepare = raw_prepare
        streaming_module.prepare = prepare

    raw_preview = cost_preview_module.chat_cost_preview
    if not getattr(raw_preview, "_ai_workspace_customer_capacity", False):

        def chat_cost_preview(*args, **kwargs):
            token_user = _current_user.set(kwargs.get("user"))
            token_rejections = _customer_rejections.set(0)
            try:
                return raw_preview(*args, **kwargs)
            except ValidationError as exc:
                raise _friendly_capacity_error(exc) from exc
            finally:
                _customer_rejections.reset(token_rejections)
                _current_user.reset(token_user)

        chat_cost_preview._ai_workspace_customer_capacity = True
        chat_cost_preview._raw_chat_cost_preview = raw_preview
        cost_preview_module.chat_cost_preview = chat_cost_preview

    # Rebind modules that may import these functions by value before AppConfig.ready().
    # Do it only for the real production module; unit tests intentionally install the
    # layer on lightweight stand-ins and must not mutate process-global Django views.
    if getattr(streaming_module, "__name__", "") != "apps.chat.streaming":
        return
    bindings = {
        "apps.chat.cost_views": {
            "chat_cost_preview": cost_preview_module.chat_cost_preview,
            "prepare": streaming_module.prepare,
        },
        "apps.chat.views": {"prepare": streaming_module.prepare},
        "apps.chat.services": {"prepare": streaming_module.prepare},
    }
    for module_name, values in bindings.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for name, value in values.items():
            if hasattr(module, name):
                setattr(module, name, value)
