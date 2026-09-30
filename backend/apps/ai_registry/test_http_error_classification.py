import httpx
import pytest

from .http_errors import classify_http_error


def _error(status, payload=None):
    request = httpx.Request("POST", "https://provider.example/v1/chat")
    response = httpx.Response(status, request=request, json=payload or {})
    return httpx.HTTPStatusError("provider error", request=request, response=response)


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "authentication_error", False),
        (402, "credit_balance_exhausted", False),
        (403, "permission_denied", False),
        (429, "rate_limited", True),
        (500, "server_error", True),
    ],
)
def test_http_statuses_map_to_operational_error_codes(status, code, retryable):
    error = classify_http_error(_error(status))
    assert error.code == code
    assert error.retryable is retryable


def test_model_not_found_payload_is_model_scoped():
    error = classify_http_error(
        _error(
            404,
            {"error": {"code": "model_not_found", "message": "Requested model does not exist"}},
        )
    )
    assert error.code == "model_not_found"
    assert error.retryable is False


def test_generic_bad_request_does_not_become_model_failure():
    error = classify_http_error(
        _error(400, {"error": {"code": "invalid_request", "message": "Malformed payload"}})
    )
    assert error.code == "http_400"
    assert error.retryable is False


def test_timeout_is_retryable_transport_failure():
    request = httpx.Request("POST", "https://provider.example/v1/chat")
    error = classify_http_error(httpx.ReadTimeout("slow", request=request))
    assert error.code == "timeout"
    assert error.retryable is True
