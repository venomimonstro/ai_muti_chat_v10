"""Stable customer-safe error classification for chat transports and history.

Provider/model/key/runtime internals belong in GenerationAttempt and diagnostics, not
in the public conversation payload. Keep the public contract intentionally tiny so
web/mobile clients can render the same state before and after a page reload.
"""

PROVIDER_ERROR_MARKERS = (
    "provider_",
    "gigachat_",
    "deepseek_",
    "openai_",
    "anthropic_",
    "openrouter_",
    "gemini_",
    "xai_",
)

PROVIDER_ERROR_CODES = {
    "timeout",
    "invalid_stream",
    "network_error",
    "rate_limited",
    "authentication_error",
    "permission_denied",
    "model_not_found",
    "unsupported_model",
    "invalid_model",
    "unknown_model",
    "invalid_api_key",
    "credential_missing",
    "credit_balance_exhausted",
    "insufficient_quota",
    "organization_usage_limit_exceeded",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "candidate_not_ready",
    "provider_funding_unavailable",
}

SAFE_PUBLIC_CODES = {
    "",
    "AI-102",
    "AI-103",
    "partial_response_interrupted",
    "client_cancelled",
}


def is_provider_error(code) -> bool:
    value = str(code or "").strip().casefold()
    return value in PROVIDER_ERROR_CODES or any(marker in value for marker in PROVIDER_ERROR_MARKERS)


def public_error_code(code) -> str:
    """Return the only error identifier a customer-facing API may expose."""
    value = str(code or "").strip()
    if value in SAFE_PUBLIC_CODES:
        return value
    if is_provider_error(value):
        return "AI-102"
    return "AI-103"
