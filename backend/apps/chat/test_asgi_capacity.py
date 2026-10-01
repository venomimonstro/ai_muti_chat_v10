import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from . import asgi_stream


class NoSlot:
    def acquire(self, *, blocking):
        assert blocking is False
        return False


def test_asgi_capacity_rejection_never_submits_provider_work():
    generation = SimpleNamespace(id="00000000-0000-0000-0000-000000000099")
    executor = Mock()
    finalized = []

    async def collect():
        return [chunk async for chunk in asgi_stream.managed_run_async(generation)]

    with (
        patch.object(asgi_stream, "_STREAM_SLOTS", NoSlot()),
        patch.object(asgi_stream, "_STREAM_EXECUTOR", executor),
        patch.object(
            asgi_stream,
            "_finalize_unhandled_failure",
            side_effect=lambda item: finalized.append(item.id),
        ),
    ):
        chunks = asyncio.run(collect())

    executor.submit.assert_not_called()
    assert finalized == [generation.id]
    assert len(chunks) == 1
    payload = json.loads(
        next(line[6:] for line in chunks[0].splitlines() if line.startswith("data: "))
    )
    assert payload["code"] == "AI-103"
    assert payload["support_code"] == "AI-103"
    assert "неподтверждённые расходы не списаны" in payload["message"]


def test_asgi_capacity_defaults_are_bounded():
    assert 4 <= asgi_stream.STREAM_EXECUTOR_WORKERS <= 128
    assert asgi_stream.STREAM_EXECUTOR_WORKERS <= asgi_stream.STREAM_MAX_INFLIGHT <= 512
