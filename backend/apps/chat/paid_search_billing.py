from __future__ import annotations

import hashlib
import os
from contextvars import ContextVar
from decimal import Decimal
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.ai_registry.models import Provider, ProviderApiKey
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
    if not _truthy("WEB_SEARCH_PAID_PROVIDERS_ENABLED", "true"):
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
    useful_snippets = sum(1 for row in rows if len(str(getattr(row, "snippet", "")).strip()) >= 60)
    return len(domains) >= 2 and useful_snippets >= max(1, len(rows) // 2)


def _mark_key(account, *, healthy: bool, error_code: str = ""):
    if not account.api_key_id:
        return
    fields = {
        "health_state": ProviderApiKey.HealthState.HEALTHY if healthy else ProviderApiKey.HealthState.DEGRADED,
        "last_error_code": "" if healthy else str(error_code or "search_failed")[:80],
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


def install(*, streaming_module, web_tools_module) -> None:
    raw_prepare = streaming_module.prepare
    raw_enrich = streaming_module.enrich_snapshot_with_web
    raw_yandex = web_tools_module._search_yandex
    raw_config = web_tools_module._yandex_search_config
    raw_searx = web_tools_module._search_searx
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
                    "folder_id": str(auth.get("folder_id") or config.get("folder_id") or "").strip(),
                    "endpoint": str(auth.get("endpoint") or provider.api_base_url or config.get("endpoint") or "").strip(),
                    "search_type": str(auth.get("search_type") or config.get("search_type") or "SEARCH_TYPE_RU").strip(),
                    "region": str(auth.get("region") or config.get("region") or "225").strip(),
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
            raise web_tools_module.WebToolError("Paid search requires a billable chat context")
        from apps.chat.models import Generation

        generation = Generation.objects.filter(
            owner=context["user"], idempotency_key=context["idempotency_key"]
        ).first()
        if generation is None:
            raise web_tools_module.WebToolError("Search generation context is unavailable")
        try:
            provider, account, unit_cost = _provider_and_account()
            quote = _search_quote(provider, unit_cost)
            with transaction.atomic():
                customer_reservation = reserve(
                    context["user"],
                    quote.user_charge_rub,
                    f"web-search:{generation.id}",
                )
                provider_reservation = reserve_provider_spend(
                    provider=provider,
                    amount_native=SEARCH_NATIVE_UNITS,
                    source_key=f"web-search:{generation.id}",
                )
        except Exception as exc:
            raise web_tools_module.WebToolError(f"Yandex Search billing unavailable: {exc}") from exc

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

        with transaction.atomic():
            spend = settle_provider_spend(
                reservation_id=provider_reservation.id if provider_reservation else None,
                actual_native=SEARCH_NATIVE_UNITS,
                nominal_cost_rub=quote.provider_cost_rub,
                customer_charge_rub=quote.user_charge_rub,
                source_type="web_search",
                source_id=str(generation.id),
                model_slug=SEARCH_PROVIDER_SLUG,
            )
            settle(customer_reservation.id, quote.user_charge_rub)
        _mark_key(account, healthy=True)
        _usage.set(
            {
                "provider": "yandex",
                "paid": True,
                "provider_cost_rub": str(spend.economic_cost_rub if spend else quote.economic_cost_rub),
                "customer_charge_rub": str(quote.user_charge_rub),
                "pricing_mode": "procurement_per_request",
                "query_sha256": hashlib.sha256(str(query).encode("utf-8")).hexdigest(),
            }
        )
        return results

    def search_web(query: str, *, limit: int = 5):
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
        raise web_tools_module.WebToolError("All web search providers failed: " + "; ".join(errors))

    def enrich(snapshot: dict, query: str, *, required: bool):
        _usage.set(None)
        result = raw_enrich(snapshot, query, required=required)
        usage = _usage.get()
        if usage and isinstance(result.get("web_search"), dict) and result["web_search"].get("used"):
            result["web_search"] = {**result["web_search"], **usage}
        return result

    def prepare(*args, **kwargs):
        token = _ctx.set(
            {
                "user": kwargs.get("user"),
                "idempotency_key": kwargs.get("idempotency_key"),
            }
        )
        try:
            return raw_prepare(*args, **kwargs)
        finally:
            _ctx.reset(token)
            _usage.set(None)

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
        base = {
            "provider_order": ["searx", "yandex"] if _paid_ready() else ["searx"],
            "searx": web_tools_module.searx_search_status(),
            "yandex": {
                **web_tools_module.yandex_search_status(),
                "customer_traffic_enabled": _paid_ready(),
                "billing_ready": configured,
                "available_requests": available,
                "acquisition_cost_rub_per_request": str(unit_cost),
                "markup_percent": markup,
                "billing_policy": "separate_tool_charge_in_answer_total",
            },
        }
        return base

    prepare._ai_workspace_paid_search_billing = True
    web_tools_module._yandex_search_config = yandex_config
    web_tools_module._search_yandex = billed_yandex
    web_tools_module.search_web = search_web
    web_tools_module.web_search_status = status
    streaming_module.enrich_snapshot_with_web = enrich
    streaming_module.prepare = prepare
