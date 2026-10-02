from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from apps.chat import asgi_stream
from apps.chat.streaming import sse


def _event_name(chunk: str) -> str:
    if not isinstance(chunk, str) or not chunk.startswith("event:"):
        return ""
    return chunk.splitlines()[0].split(":", 1)[1].strip()


class Command(BaseCommand):
    help = "Verify native ASGI executor/queue/SSE delivery without database or provider calls."

    def handle(self, *args, **options):
        generation = SimpleNamespace(
            id=uuid.uuid4(),
            state="queued",
            correlation_id=uuid.uuid4(),
        )
        original = asgi_stream.managed_run

        def fake_managed_run(_generation):
            yield sse("generation", {"id": str(_generation.id), "state": "running"})
            yield sse("delta", {"text": "transport-ok"})
            yield sse(
                "completed",
                {
                    "state": "completed",
                    "cost_rub": "0",
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "model": "transport-smoke",
                    "provider": "system",
                },
            )

        async def consume():
            events = []
            async for chunk in asgi_stream.managed_run_async(
                generation,
                heartbeat_seconds=1.0,
            ):
                name = _event_name(chunk)
                if name and name != "heartbeat":
                    events.append(name)
            return events

        asgi_stream.managed_run = fake_managed_run
        try:
            events = asyncio.run(consume())
        finally:
            asgi_stream.managed_run = original

        expected = ["generation", "delta", "completed"]
        if events != expected:
            raise CommandError(
                "CHAT_ASGI_TRANSPORT_BROKEN "
                f"expected={expected} actual={events}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                "CHAT_ASGI_TRANSPORT_OK events=" + ",".join(events)
            )
        )
