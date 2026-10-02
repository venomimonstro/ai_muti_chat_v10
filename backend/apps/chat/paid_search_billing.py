from __future__ import annotations

import hashlib
import logging
import os
from contextvars import ContextVar
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.signals import post_save
from django.utils import timezone

from apps.ai_registry.models import Provider, ProviderApiKey
from apps.billing.models import BalanceReservation
from apps.billing.pricing import quote_flat, require_margin
from apps.billing.services import release, reserve, settle
from apps.procurement.services import (
    account_available_native,
    account_weighted_unit_cost_rub,
    default_account,
    release_provider_spend,
    reserve_provider_spend,
    settle_provider_spend,
)

logger = logging.getLogger(__name__)
SEARCH_PROVIDER_SLUG = "yandex-search"
SEARCH_NATIVE_UNITS = Decimal("1")
_ctx = ContextVar("chat_paid_search_context", default=None)
_usage = ContextVar("chat_paid_search_usage", default=None)

PREMIUM_EXPLICIT = (
    "яндекс", "yandex", "поиск яндекс", "в яндексе",
)
PREMIUM_LOCAL = (
    "в москве", "в санкт-петербурге", "в петербурге", "в спб", "в россии",
    "в казани", "в екатеринбурге", "в новосибирске", "в сочи", "в перми",
    "в уфе", "в тюмени", "в челябинске", "в красноярске", "рядом со мной", "поблизости",
)
PREMIUM_INTENT = (
    "лучшие", "топ ", "рейтинг", "отзывы", "где купить", "где найти", "найди",
    "стоматолог", "клиник", "ресторан", "отель", "магазин", "сервис", "ваканси",
    "цена", "стоимость", "наличие",
)


def _truthy(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().casefold() in {"1", "true", "yes", "on"}


def _provider_and_account():
    provider = Provider.objects.filter(
        slug=SEARCH_PROVIDER_SLUG,
        enabled=True,
        emergency_disabled=False,
    ).first()
    if provider is None:
        raise ValidationError("Yandex Search provider не настроен")
    account = default_account(provider)
    if account is None or not account.active:
        raise ValidationError("Для Yandex Search не настроен основной закупочный аккаунт")
    if account_available_native(account) < SEARCH_NATIVE_UNITS:
        raise ValidationError("Закупленный баланс Yandex Search исчерпан")
    unit_cost = account_weighted_unit_cost_rub(account)
    if unit_cost <= 0:
        raise ValidationError("Для Yandex Search не задана стоимость закупки")
    return provider, account, unit_cost


def _account_secret(account) -> str:
    if account.api_key_id:
        key = account.api_key
        if not key.enabled or key.health_state == ProviderApiKey.HealthState.DISABLED:
            return ""
        return key.get_secret()
    if account.credential_env:
        return os.getenv(account.credential_env, "").strip()
    return ""


def _paid_ready() -> bool:
    # Paid search is fail-closed. A funded key alone must never activate customer
    # charges; the operator must explicitly enable the provider and confirm that the
    # unified billing path is ready.
    if not _truthy("WEB_SEARCH_PAID_PROVIDERS_ENABLED", "false"):
        return False
    if not _truthy("WEB_SEARCH_PAID_BILLING_READY", "false"):
        return False
    try:
        _provider, account, _unit = _provider_and_account()
        return bool(_account_secret(account))
    except Exception:
        return False


def _premium_search(query: str) -> bool:
    text = " ".join(str(query or "").casefold().split())
    if any(marker in text for marker in PREMIUM_EXPLICIT):
        return True
    return any(marker in text for marker in PREMIUM_LOCAL) and any(
        marker in text for marker in PREMIUM_INTENT
    )


def _searx_adequate(results, limit: int) -> bool:
    rows = list(results or [])
    if not rows:
        return False
    minimum = min(max(2, int(limit or 5) // 2), 4)
    if len(rows) < minimum:
        return False
    domains = {
        (urlparse(str(getattr(row, "url", ""))).hostname or "").removeprefix("www.")
        for row in rows
        if getattr(row, "url", "")
    }
    useful_snippets = sum(
        1 for row in rows if len(str(getattr(row, "snippet", "")).strip()) >= 60
    )
    return len(domains) >= 2 and useful_snippets >= max(1, len(rows) // 2)


def _mark_key(account, *, healthy: bool, error_code: str = ""):
    if not account.api_key_id:
        return
    fields = {
        "health_state": (
            ProviderApiKey.HealthState.HEALTHY
            if healthy
            else ProviderApiKey.HealthState.DEGRADED
        ),
        "last_error_code": "" if healthy else str(error_code or "search_failed")[:80],
        "last_checked_at": timezone.now(),
    }
    ProviderApiKey.objects.filter(pk=account.api_key_id).update(**fields)


def _search_quote(provider, unit_cost_rub):
    base_markup = Decimal(str((provider.auth_config or {}).get("markup_percent", "100")))
    return require_margin(
        quote_flat(
            provider_cost_native=unit_cost_rub,
            provider_currency="RUB",
            base_markup_percent=base_markup,
            provider_slug=provider.slug,
            model_slug=SEARCH_PROVIDER_SLUG,
            operation_type="web_search",
        )
    )


def expected_search_charge(query: str) -> Decimal:
    """Upper-bound customer charge for one possible paid web-search call.

    Free SearXNG remains the normal first path, so this value belongs to the preview
    maximum, never its minimum. Returning zero when search is not required or paid
    search is not commercially ready keeps the preview honest and avoids false cost
    confirmations for timeless prompts.
    """
    from .search_trigger_policy import search_required

    if not search_required(query) or not _paid_ready():
        return Decimal("0")
    try:
        provider, _account, unit_cost = _provider_and_account()
        return _search_quote(provider, unit_cost).user_charge_rub
    except Exception:
        return Decimal("0")


def public_search_charge(generation) -> Decimal:
    reservation = BalanceReservation.objects.filter(
        idempotency_key=f"web-search:{generation.id}",
        state=BalanceReservation.State.SETTLED,
    ).only("actual_rub").first()
    return Decimal(str(reservation.actual_rub or 0)) if reservation else Decimal("0")


def _finish_customer_search_charge(generation):
    reservation = BalanceReservation.objects.filter(
        idempotency_key=f"web-search:{generation.id}"
    ).first()
    if reservation is None or reservation.state != BalanceReservation.State.ACTIVE:
        return reservation
    if generation.state == generation.State.COMPLETED:
        return settle(reservation.id, reservation.amount_rub)
    if generation.state in {generation.State.FAILED, generation.State.CANCELLED}:
        return release(reservation.id)
    return reservation


def recover_search_reservations(*, older_than_seconds: int = 900, limit: int = 200) -> dict:
    from apps.chat.models import Generation

    cutoff = timezone.now() - timedelta(seconds=max(60, int(older_than_seconds)))
    rows = list(
        BalanceReservation.objects.filter(
            state=BalanceReservation.State.ACTIVE,
            idempotency_key__startswith="web-search:",
            created_at__lt=cutoff,
        ).order_by("created_at")[: max(1, min(int(limit), 1000))]
    )
    settled = 0
    released = 0
    deferred = 0
    for reservation in rows:
        generation_id = reservation.idempotency_key.split("web-search:", 1)[-1]
        generation = Generation.objects.filter(pk=generation_id).first()
        try:
            if generation is None:
                release(reservation.id)
                released += 1
            elif generation.state == Generation.State.COMPLETED:
                settle(reservation.id, reservation.amount_rub)
                settled += 1
            elif generation.state in {Generation.State.FAILED, Generation.State.CANCELLED}:
                release(reservation.id)
                released += 1
            else:
                deferred += 1
        except Exception:
            logger.exception("Search reservation recovery failed reservation_id=%s", reservation.id)
            deferred += 1
    return {
        "checked": len(rows),
        "settled": settled,
        "released": released,
        "deferred": deferred,
    }


def install(*, streaming_module, web_tools_module) -> None:
    raw_prepare = streaming_module.prepare
    raw_enrich = streaming_module.enrich_snapshot_with_web
    raw_yandex = web_tools_module._search_yandex
    raw_config = web_tools_module._yandex_search_config
    raw_searx = web_tools_module._search_searx
    raw_search_web = web_tools_module.search_web
    if getattr(raw_prepare, "_ai_workspace_paid_search_billing", False):
        return

    def yandex_config():
        config = raw_config()
        try:
            provider, account, _unit = _provider_and_account()
            secret = _account_secret(account)
            if not secret:
                config["api_key"] = ""
                config["source"] = "none"
                return config
            auth = provider.auth_config or {}
            config.update(
                {
                    "api_key": secret,
                    "folder_id": str(
                        auth.get("folder_id") or config.get("folder_id") or ""
                    ).strip(),
                    "endpoint": str(
                        auth.get("endpoint")
                        or provider.api_base_url
                        or config.get("endpoint")
                        or ""
                    ).strip(),
                    "search_type": str(
                        auth.get("search_type")
                        or config.get("search_type")
                        or "SEARCH_TYPE_RU"
                    ).strip(),
                    "region": str(
                        auth.get("region") or config.get("region") or "225"
                    ).strip(),
                    "source": "funding_account",
                }
            )
        except Exception:
            config["api_key"] = ""
            config["source"] = "none"
        return config

    def billed_yandex(query: str, *, limit: int):
        context = _ctx.get()
        if not context:
            # Registry diagnostics and explicit internal search are not customer
            # chat operations. Preserve the transport contract outside chat; the
            # provider-order billing gate still controls whether Yandex is enabled.
            return raw_yandex(query, limit=limit)
        from apps.chat.models import Generation

        generation = Generation.objects.filter(
            owner=context["user"], idempotency_key=context["idempotency_key"]
        ).first()
        if generation is None:
            raise web_tools_module.WebToolError("Search generation context is unavailable")
        try:
            provider, account, unit_cost = _provider_and_account()
            search_quote = _search_quote(provider, unit_cost)
            with transaction.atomic():
                customer_reservation = reserve(
                    context["user"],
                    search_quote.user_charge_rub,
                    f"web-search:{generation.id}",
                )
                provider_reservation = reserve_provider_spend(
                    provider=provider,
                    amount_native=SEARCH_NATIVE_UNITS,
                    source_key=f"web-search:{generation.id}",
                )
        except Exception as exc:
            raise web_tools_module.WebToolError(
                f"Yandex Search billing unavailable: {exc}"
            ) from exc

        try:
            results = raw_yandex(query, limit=limit)
        except Exception as exc:
            with transaction.atomic():
                try:
                    release(customer_reservation.id)
                finally:
                    if provider_reservation is not None:
                        release_provider_spend(provider_reservation.id)
            code = "search_failed"
            text = str(exc).casefold()
            if "401" in text or "403" in text:
                code = "authentication_error"
            elif "402" in text:
                code = "credit_balance_exhausted"
            _mark_key(account, healthy=False, error_code=code)
            raise

        # The upstream search expense is real as soon as Yandex returns results, so
        # procurement is settled immediately. The customer's search reservation stays
        # ACTIVE until the answer reaches COMPLETED. If the LLM/preflight fails later,
        # the customer is refunded and the platform bears the tool expense rather than
        # charging for an answer that was never delivered.
        with transaction.atomic():
            spend = settle_provider_spend(
                reservation_id=(provider_reservation.id if provider_reservation else None),
                actual_native=SEARCH_NATIVE_UNITS,
                nominal_cost_rub=search_quote.provider_cost_rub,
                customer_charge_rub=Decimal("0"),
                source_type="web_search",
                source_id=str(generation.id),
                model_slug=SEARCH_PROVIDER_SLUG,
            )
        _mark_key(account, healthy=True)
        _usage.set(
            {
                "provider": "yandex",
                "paid": True,
                "provider_cost_rub": str(
                    spend.economic_cost_rub if spend else search_quote.economic_cost_rub
                ),
                "customer_charge_rub": str(search_quote.user_charge_rub),
                "pricing_mode": "procurement_per_request",
                "query_sha256": hashlib.sha256(str(query).encode("utf-8")).hexdigest(),
                "customer_reservation_id": str(customer_reservation.id),
            }
        )
        return results

    def search_web(query: str, *, limit: int = 5):
        # Keep the registry-level search service independent from chat billing.
        # Only a real chat prepare() establishes _ctx. Health checks, diagnostics,
        # tests and other internal callers retain ordinary provider failover.
        if not _ctx.get():
            return raw_search_web(query, limit=limit)

        premium = _premium_search(query)
        paid = _paid_ready()
        errors = []
        weak_free = None
        order = ["yandex", "searx"] if premium and paid else ["searx", "yandex"]
        for provider_name in order:
            if provider_name == "yandex":
                if not paid:
                    continue
                try:
                    results = billed_yandex(query, limit=limit)
                    metadata = dict(_usage.get() or {})
                    metadata["decision"] = "premium" if premium else "free_fallback"
                    _usage.set(metadata)
                    return results
                except web_tools_module.WebToolError as exc:
                    errors.append(f"yandex: {exc}")
                    continue
            try:
                results = raw_searx(query, limit=limit)
                if _searx_adequate(results, limit) or not paid:
                    _usage.set(
                        {
                            "provider": "searx",
                            "paid": False,
                            "provider_cost_rub": "0",
                            "customer_charge_rub": "0",
                            "decision": "free",
                        }
                    )
                    return results
                weak_free = results
                errors.append("searx: insufficient quality")
            except web_tools_module.WebToolError as exc:
                errors.append(f"searx: {exc}")
        if weak_free:
            _usage.set(
                {
                    "provider": "searx",
                    "paid": False,
                    "provider_cost_rub": "0",
                    "customer_charge_rub": "0",
                    "decision": "free_degraded",
                }
            )
            return weak_free
        raise web_tools_module.WebToolError(
            "All web search providers failed: " + "; ".join(errors)
        )

    def enrich(snapshot: dict, query: str, *, required: bool):
        _usage.set(None)
        result = raw_enrich(snapshot, query, required=required)
        usage = dict(_usage.get() or {})
        if usage and isinstance(result.get("web_search"), dict) and result["web_search"].get("used"):
            reservation_id = usage.pop("customer_reservation_id", "")
            result["web_search"] = {**result["web_search"], **usage}
            if reservation_id:
                result["internal_search_billing"] = {
                    "customer_reservation_id": reservation_id,
                    "customer_charge_rub": usage.get("customer_charge_rub", "0"),
                    "provider": usage.get("provider", ""),
                }
        return result

    def prepare(*args, **kwargs):
        token = _ctx.set(
            {
                "user": kwargs.get("user"),
                "idempotency_key": kwargs.get("idempotency_key"),
            }
        )
        _usage.set(None)
        try:
            return raw_prepare(*args, **kwargs)
        except BaseException:
            usage = dict(_usage.get() or {})
            reservation_id = usage.get("customer_reservation_id")
            if reservation_id:
                try:
                    release(reservation_id)
                except Exception:
                    logger.exception(
                        "Failed to release paid-search customer reserve id=%s",
                        reservation_id,
                    )
            raise
        finally:
            _ctx.reset(token)
            _usage.set(None)

    def generation_saved(sender, instance, **kwargs):
        if instance.state not in {
            instance.State.COMPLETED,
            instance.State.FAILED,
            instance.State.CANCELLED,
        }:
            return
        try:
            _finish_customer_search_charge(instance)
        except Exception:
            # Terminal answer state must never be rolled back by a secondary billing
            # hook. The periodic recovery task will retry the exact idempotent close.
            logger.exception(
                "Paid-search customer settlement failed generation_id=%s",
                instance.id,
            )

    def status():
        try:
            provider, account, unit_cost = _provider_and_account()
            configured = bool(_account_secret(account))
            available = str(account_available_native(account))
            markup = str((provider.auth_config or {}).get("markup_percent", "100"))
        except Exception:
            configured = False
            available = "0"
            unit_cost = Decimal("0")
            markup = ""
        unified_billing_ready = _truthy("WEB_SEARCH_PAID_BILLING_READY", "false")
        paid_ready = _paid_ready()
        return {
            "provider_order": ["searx", "yandex"] if paid_ready else ["searx"],
            "searx": web_tools_module.searx_search_status(),
            "yandex": {
                **web_tools_module.yandex_search_status(),
                "customer_traffic_enabled": paid_ready,
                "billing_ready": bool(configured and unified_billing_ready),
                "available_requests": available,
                "acquisition_cost_rub_per_request": str(unit_cost),
                "markup_percent": markup,
                "billing_policy": (
                    "charge_only_when_answer_delivered"
                    if unified_billing_ready
                    else "blocked_until_unified_billing"
                ),
            },
        }

    prepare._ai_workspace_paid_search_billing = True
    web_tools_module._yandex_search_config = yandex_config
    web_tools_module._search_yandex = billed_yandex
    web_tools_module.search_web = search_web
    web_tools_module.web_search_status = status
    streaming_module.enrich_snapshot_with_web = enrich
    streaming_module.prepare = prepare

    from apps.chat.models import Generation

    post_save.connect(
        generation_saved,
        sender=Generation,
        dispatch_uid="chat.paid_search_billing.generation_terminal",
        weak=False,
    )
