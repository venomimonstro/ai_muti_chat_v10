from __future__ import annotations

import httpx

from .adapters import ProviderError


MODEL_ERROR_CODES = {
    "model_not_found",
    "unknown_model",
    "unsupported_model",
    "invalid_model",
}
AUTH_ERROR_CODES = {
    "invalid_api_key",
    "authentication_error",
    "unauthorized",
    "invalid_authentication",
}
QUOTA_ERROR_CODES = {
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing_hard_limit_reached",
    "insufficient_credits",
}


def _response_error(response):
    if response is None:
        return "", ""
    try:
        payload = response.json()
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return "", ""
    error = payload.get("error")
    if not isinstance(error, dict):
        error = payload
    code = str(error.get("code") or error.get("type") or "").strip().casefold()
    message = str(error.get("message") or payload.get("message") or "").strip()
    return code, message


def classify_http_error(exc: httpx.HTTPError) -> ProviderError:
    """Map transport failures to stable runtime semantics used by circuit breakers."""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("Provider timeout", code="timeout", retryable=True)

    response = getattr(exc, "response", None)
    status = response.status_code if response is not None else None
    upstream_code, upstream_message = _response_error(response)
    normalized_message = upstream_message.casefold()

    if upstream_code in MODEL_ERROR_CODES or (
        status in {400, 404}
        and "model" in normalized_message
        and any(token in normalized_message for token in ("not found", "does not exist", "unknown", "unsupported", "invalid"))
    ):
        return ProviderError(
            upstream_message or "Provider model is unavailable",
            code="model_not_found",
            retryable=False,
        )

    if status == 401 or upstream_code in AUTH_ERROR_CODES:
        return ProviderError(
            upstream_message or "Provider authentication failed",
            code="authentication_error",
            retryable=False,
        )
    if status == 402 or upstream_code in QUOTA_ERROR_CODES:
        return ProviderError(
            upstream_message or "Provider credits are exhausted",
            code="credit_balance_exhausted",
            retryable=False,
        )
    if status == 403:
        return ProviderError(
            upstream_message or "Provider permission denied",
            code="permission_denied",
            retryable=False,
        )
    if status == 429:
        return ProviderError(
            upstream_message or "Provider rate limit",
            code="rate_limited",
            retryable=True,
        )
    if status is not None and status >= 500:
        return ProviderError(
            upstream_message or "Provider service error",
            code="server_error",
            retryable=True,
        )
    if status is not None and 400 <= status < 500:
        return ProviderError(
            upstream_message or "Provider rejected request",
            code=f"http_{status}",
            retryable=False,
        )
    return ProviderError(
        upstream_message or "Provider request failed",
        code="network_error" if status is None else f"http_{status}",
        retryable=True,
    )


def install(adapters_module) -> None:
    adapters_module._http_error = classify_http_error
