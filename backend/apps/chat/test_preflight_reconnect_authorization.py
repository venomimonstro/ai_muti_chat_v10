import asyncio
from types import SimpleNamespace

import pytest

from . import cost_views
from .models import Generation


@pytest.mark.parametrize("native_async", [False, True])
def test_reconnect_waits_for_durable_cost_authorization_before_provider_execution(monkeypatch, native_async):
    states = iter([
        SimpleNamespace(id="generation", state=Generation.State.QUEUED, error_code="", context_snapshot={}),
        SimpleNamespace(id="generation", state=Generation.State.QUEUED, error_code="", context_snapshot={"customer_stream_authorized": True}),
    ])
    calls = []
    monkeypatch.setattr(cost_views, "_preparing_snapshot", lambda generation_id: next(states))
    monkeypatch.setattr(cost_views.time, "sleep", lambda delay: None)

    async def no_sleep(delay):
        pass

    monkeypatch.setattr(cost_views.asyncio, "sleep", no_sleep)

    def run(current):
        assert current.context_snapshot["customer_stream_authorized"]
        calls.append(current.id)
        yield 'event: completed\ndata: {"state":"completed"}\n\n'

    async def run_async(current):
        for chunk in run(current):
            yield chunk

    monkeypatch.setattr(cost_views, "managed_run", run)
    monkeypatch.setattr(cost_views, "managed_run_async", run_async)
    generation = SimpleNamespace(id="generation", state=Generation.State.QUEUED)
    if native_async:
        async def collect():
            return [chunk async for chunk in cost_views._wait_for_authorized_stream_async(generation)]
        chunks = asyncio.run(collect())
    else:
        chunks = list(cost_views._wait_for_authorized_stream(generation))
    assert chunks[0].startswith("event: generation")
    assert chunks[1].startswith("event: heartbeat")
    assert chunks[-1].startswith("event: completed")
    assert calls == ["generation"]


@pytest.mark.parametrize("native_async", [False, True])
def test_changed_cost_reopens_confirmation_without_executing_a_provider(monkeypatch, native_async):
    generation = SimpleNamespace(id="generation", state=Generation.State.QUEUED)
    current = SimpleNamespace(id="generation", state=Generation.State.QUEUED, error_code=cost_views.COST_CONFIRMATION_CHANGED, context_snapshot={})
    monkeypatch.setattr(cost_views, "_preparing_snapshot", lambda generation_id: current)

    def forbidden(*args, **kwargs):
        raise AssertionError("unconfirmed generation must not call provider")

    monkeypatch.setattr(cost_views, "managed_run", forbidden)
    monkeypatch.setattr(cost_views, "managed_run_async", forbidden)
    if native_async:
        async def collect():
            return [chunk async for chunk in cost_views._wait_for_authorized_stream_async(generation)]
        chunks = asyncio.run(collect())
    else:
        chunks = list(cost_views._wait_for_authorized_stream(generation))
    assert len(chunks) == 2
    assert "awaiting_confirmation" in chunks[-1]
    assert not any("event: error" in chunk for chunk in chunks)


def test_async_follower_acknowledges_identity_and_replaces_running_prefix(monkeypatch):
    from . import asgi_stream

    snapshots = iter([
        {"state": "running", "text": "Начало", "cost_rub": "0"},
        {"state": "running", "text": "Начало ответа", "cost_rub": "0"},
        {"state": "completed", "text": "Начало ответа.", "cost_rub": "1"},
    ])
    monkeypatch.setattr(asgi_stream, "_generation_snapshot", lambda generation_id: next(snapshots))

    async def collect():
        return [chunk async for chunk in asgi_stream.follow_generation_async(
            SimpleNamespace(id="generation", state=Generation.State.RUNNING), poll_seconds=0
        )]

    chunks = asyncio.run(collect())
    assert chunks[0].startswith("event: generation")
    updates = [chunk for chunk in chunks if chunk.startswith("event: snapshot")]
    assert len(updates) == 3
    assert '"text": "Начало ответа"' in updates[1]
    assert '"state": "completed"' in updates[-1]
