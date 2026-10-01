from __future__ import annotations

import base64
import binascii
import os
import uuid
from decimal import Decimal

import httpx
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.ai_registry.web_tools import WebToolError, _parse_yandex_xml
from apps.chat.paid_search_billing import (
    SEARCH_NATIVE_UNITS,
    SEARCH_PROVIDER_SLUG,
    _account_secret,
    _mark_key,
    _provider_and_account,
    _search_quote,
)
from apps.procurement.services import (
    release_provider_spend,
    reserve_provider_spend,
    settle_provider_spend,
)


def probe_yandex_search(query: str = "официальный сайт Яндекс", *, limit: int = 3):
    provider, account, unit_cost = _provider_and_account()
    secret = _account_secret(account)
    if not secret:
        raise WebToolError("Yandex Search API key is unavailable")
    auth = provider.auth_config or {}
    folder_id = str(auth.get("folder_id") or os.getenv("YANDEX_SEARCH_FOLDER_ID", "")).strip()
    if not folder_id:
        raise WebToolError("Yandex Search Folder ID is not configured")
    endpoint = str(
        auth.get("endpoint")
        or provider.api_base_url
        or os.getenv("YANDEX_SEARCH_ENDPOINT", "https://searchapi.api.cloud.yandex.net/v2/web/search")
    ).strip()
    search_type = str(auth.get("search_type") or "SEARCH_TYPE_RU").strip()
    region = str(auth.get("region") or "225").strip()
    max_results = max(1, min(int(limit), 5))
    quote = _search_quote(provider, unit_cost)
    probe_id = f"admin-probe:{uuid.uuid4()}"

    try:
        reservation = reserve_provider_spend(
            provider=provider,
            amount_native=SEARCH_NATIVE_UNITS,
            source_key=probe_id,
        )
    except Exception as exc:
        raise WebToolError(f"Yandex Search procurement is unavailable: {exc}") from exc

    body = {
        "query": {
            "searchType": search_type,
            "queryText": str(query or "")[:400],
            "familyMode": "FAMILY_MODE_NONE",
            "fixTypoMode": "FIX_TYPO_MODE_ON",
        },
        "groupSpec": {
            "groupMode": "GROUP_MODE_FLAT",
            "groupsOnPage": str(max_results),
            "docsInGroup": "1",
        },
        "maxPassages": "2",
        "region": region,
        "l10n": "LOCALIZATION_RU",
        "folderId": folder_id,
        "responseFormat": "FORMAT_XML",
        "userAgent": "AIWorkspace-AdminProbe/1.0",
    }
    timeout = float(os.getenv("WEB_TOOL_TIMEOUT_SECONDS", "12"))
    try:
        response = httpx.post(
            endpoint,
            headers={"Authorization": f"Api-Key {secret}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        encoded = payload.get("rawData") if isinstance(payload, dict) else None
        if not encoded:
            raise WebToolError("Yandex Search returned empty rawData")
        raw_xml = base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")
        results = _parse_yandex_xml(raw_xml, max_results)
        if not results:
            raise WebToolError("Yandex Search returned no usable results")
    except Exception as exc:
        with transaction.atomic():
            release_provider_spend(reservation.id)
        text = str(exc).casefold()
        error_code = "search_failed"
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            error_code = (
                "authentication_error"
                if status in {401, 403}
                else "credit_balance_exhausted"
                if status == 402
                else f"http_{status}"
            )
        elif isinstance(exc, (binascii.Error, ValueError, TypeError)):
            error_code = "invalid_response"
        elif "401" in text or "403" in text:
            error_code = "authentication_error"
        elif "402" in text:
            error_code = "credit_balance_exhausted"
        if error_code in {"authentication_error", "credit_balance_exhausted"}:
            _mark_key(account, healthy=False, error_code=error_code)
        if isinstance(exc, WebToolError):
            raise
        if isinstance(exc, httpx.HTTPStatusError):
            raise WebToolError(f"Yandex Search HTTP {exc.response.status_code}") from exc
        raise WebToolError("Yandex Search provider failed") from exc

    with transaction.atomic():
        settle_provider_spend(
            reservation_id=reservation.id,
            actual_native=SEARCH_NATIVE_UNITS,
            nominal_cost_rub=quote.provider_cost_rub,
            customer_charge_rub=Decimal("0"),
            source_type="web_search_probe",
            source_id=probe_id,
            model_slug=SEARCH_PROVIDER_SLUG,
        )
    _mark_key(account, healthy=True)
    return results
