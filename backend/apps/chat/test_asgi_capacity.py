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
    assert len(chunks) == 2
    accepted = json.loads(
        next(line[6:] for line in chunks[0].splitlines() if line.startswith("data: "))
    )
    assert chunks[0].startswith("event: generation\n")
    assert accepted["id"] == generation.id
    assert accepted["accepted"] is True
    payload = json.loads(
        next(line[6:] for line in chunks[1].splitlines() if line.startswith("data: "))
    )
    assert payload["code"] == "AI-103"
    assert payload["support_code"] == "AI-103"
    assert "неподтверждённые расходы не списаны" in payload["message"]


def test_asgi_capacity_defaults_are_bounded():
    assert 4 <= asgi_stream.STREAM_EXECUTOR_WORKERS <= 128
    assert asgi_stream.STREAM_EXECUTOR_WORKERS <= asgi_stream.STREAM_MAX_INFLIGHT <= 512


def test_asgi_executor_start_failure_still_acknowledges_durable_generation():
    generation = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000100",
        state="queued",
    )
    finalized = []

    class Slot:
        def acquire(self, *, blocking):
            assert blocking is False
            return True

        def release(self):
            return None

    executor = Mock()
    executor.submit.side_effect = RuntimeError("executor unavailable")

    async def collect():
        return [chunk async for chunk in asgi_stream.managed_run_async(generation)]

    with (
        patch.object(asgi_stream, "_STREAM_SLOTS", Slot()),
        patch.object(asgi_stream, "_STREAM_EXECUTOR", executor),
        patch.object(
            asgi_stream,
            "_finalize_unhandled_failure",
            side_effect=lambda item: finalized.append(item.id),
        ),
    ):
        chunks = asyncio.run(collect())

    assert finalized == [generation.id]
    assert len(chunks) == 2
    assert chunks[0].startswith("event: generation\n")
    assert chunks[1].startswith("event: error\n")


def test_asgi_losing_reconnect_claim_becomes_read_only_follower():
    generation = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000101",
        state="queued",
    )

    class Slot:
        def acquire(self, *, blocking):
            assert blocking is False
            return True

        def release(self):
            return None

    class ClaimLosingExecutor:
        def submit(self, _fn, _generation, _loop, queue, _detached):
            queue.put_nowait((
                "chunk",
                'event: error\ndata: {"code":"generation_in_progress"}\n\n',
            ))
            queue.put_nowait(("done", None))
            return Mock()

    snapshots = iter([
        {
            "state": "running",
            "error_code": "",
            "cost_rub": "0",
            "text": "Уже идёт ответ",
            "message_status": "streaming",
        },
        {
            "state": "completed",
            "error_code": "",
            "cost_rub": "0.5000",
            "text": "Готовый ответ",
            "message_status": "completed",
        },
    ])

    async def collect():
        return [chunk async for chunk in asgi_stream.managed_run_async(generation)]

    with (
        patch.object(asgi_stream, "_STREAM_SLOTS", Slot()),
        patch.object(asgi_stream, "_STREAM_EXECUTOR", ClaimLosingExecutor()),
        patch.object(
            asgi_stream,
            "_generation_snapshot",
            side_effect=lambda _generation_id: next(snapshots),
        ),
    ):
        chunks = asyncio.run(collect())

    assert chunks[0].startswith("event: generation\n")
    assert not any("generation_in_progress" in chunk for chunk in chunks)
    assert any(chunk.startswith("event: snapshot\n") for chunk in chunks)
    assert any('"state": "completed"' in chunk for chunk in chunks)
