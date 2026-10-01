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


@pytest.mark.parametrize(
    "internal_code",
    ["stream_runtime_failed", "stream_incomplete", "cost_or_internal_error", "agent_runtime_failed"],
)
def test_internal_runtime_codes_are_never_exposed_to_customer(internal_code):
    raw = sse(
        "error",
        {
            "code": internal_code,
            "message": "internal stack/runtime detail",
        },
    )

    public = _public_chunk(raw)
    payload = json.loads(next(line[6:] for line in public.splitlines() if line.startswith("data: ")))

    assert payload["code"] == "AI-103"
    assert payload["support_code"] == "AI-103"
    assert payload["cause_code"] == internal_code
    assert "stack" not in payload["message"].lower()
    assert internal_code not in payload["message"]


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


def test_partial_response_contract_is_preserved_for_frontend():
    raw = sse(
        "error",
        {
            "code": "partial_response_interrupted",
            "cost_rub": "0.2000",
            "partial": True,
            "message": "Полученная часть сохранена",
        },
    )
    assert _public_chunk(raw) == raw


def test_reconnect_reports_confirmed_partial_charge_truthfully():
    generation = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
    snapshot = {
        "state": Generation.State.FAILED,
        "error_code": "timeout",
        "cost_rub": "0.7500",
        "text": "частичный ответ",
        "message_status": "partial",
    }

    async def collect():
        return [chunk async for chunk in follow_generation_async(generation, poll_seconds=0.01)]

    with patch("apps.chat.asgi_stream._generation_snapshot", return_value=snapshot):
        chunks = asyncio.run(collect())

    error = next(chunk for chunk in chunks if chunk.startswith("event: error\n"))
    payload = json.loads(next(line[6:] for line in error.splitlines() if line.startswith("data: ")))
    assert payload["code"] == "AI-102"
    assert payload["cost_rub"] == "0.7500"
    assert "0.7500" in payload["message"]
    assert "не спис" not in payload["message"].lower()


def test_reconnect_hides_internal_runtime_failure_code():
    generation = SimpleNamespace(id="00000000-0000-0000-0000-000000000002")
    snapshot = {
        "state": Generation.State.FAILED,
        "error_code": "stream_runtime_failed",
        "cost_rub": "0",
        "text": "",
        "message_status": "failed",
    }

    async def collect():
        return [chunk async for chunk in follow_generation_async(generation, poll_seconds=0.01)]

    with patch("apps.chat.asgi_stream._generation_snapshot", return_value=snapshot):
        chunks = asyncio.run(collect())

    error = next(chunk for chunk in chunks if chunk.startswith("event: error\n"))
    payload = json.loads(next(line[6:] for line in error.splitlines() if line.startswith("data: ")))
    assert payload["code"] == "AI-103"
    assert payload["support_code"] == "AI-103"
    assert "stream_runtime_failed" not in payload["message"]
