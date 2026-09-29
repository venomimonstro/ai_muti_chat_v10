import json

import pytest

from .asgi_stream import _public_chunk
from .streaming import sse


@pytest.mark.parametrize(
    "internal_code",
    [
        "credit_balance_exhausted",
        "authentication_error",
        "invalid_api_key",
        "rate_limited",
        "openrouter_402",
        "gigachat_quota_exhausted",
        "provider_unavailable",
    ],
)
def test_provider_errors_are_hidden_from_customer(internal_code):
    raw = sse(
        "error",
        {
            "code": internal_code,
            "message": "internal provider detail that customer must never see",
        },
    )

    public = _public_chunk(raw)
    lines = public.splitlines()
    payload = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))

    assert payload["code"] == "AI-102"
    assert payload["support_code"] == "AI-102"
    assert "provider" not in payload["message"].lower()
    assert "api" not in payload["message"].lower()
    assert "администратор" not in payload["message"].lower()


def test_generation_in_progress_remains_internal_reconnect_signal():
    raw = sse("error", {"code": "generation_in_progress"})
    assert _public_chunk(raw) == raw
