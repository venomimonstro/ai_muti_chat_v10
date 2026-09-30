import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from .asgi_stream import _public_chunk, follow_generation_async
from .models import Generation
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


def test_provider_error_preserves_confirmed_charge_message():
    raw = sse(
        "error",
        {
            "code": "timeout",
            "cost_rub": "1.2500",
            "partial": True,
            "message": "Списана подтверждённая стоимость",
        },
    )
    public = _public_chunk(raw)
    payload = json.loads(next(line[6:] for line in public.splitlines() if line.startswith("data: ")))

    assert payload["code"] == "AI-102"
    assert payload["cost_rub"] == "1.2500"
    assert "1.2500" in payload["message"]
    assert "не спис" not in payload["message"].lower()


def test_generation_in_progress_remains_internal_reconnect_signal():
    raw = sse("error", {"code": "generation_in_progress"})
    assert _public_chunk(raw) == raw


@pytest.mark.asyncio
async def test_reconnect_reports_confirmed_partial_charge_truthfully():
    generation = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
    snapshot = {
        "state": Generation.State.FAILED,
        "error_code": "timeout",
        "cost_rub": "0.7500",
        "text": "частичный ответ",
        "message_status": "partial",
    }
    with patch("apps.chat.asgi_stream._generation_snapshot", return_value=snapshot):
        chunks = [chunk async for chunk in follow_generation_async(generation, poll_seconds=0.01)]

    error = next(chunk for chunk in chunks if chunk.startswith("event: error\n"))
    payload = json.loads(next(line[6:] for line in error.splitlines() if line.startswith("data: ")))
    assert payload["code"] == "AI-102"
    assert payload["cost_rub"] == "0.7500"
    assert "0.7500" in payload["message"]
    assert "не спис" not in payload["message"].lower()
