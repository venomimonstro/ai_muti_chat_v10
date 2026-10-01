from __future__ import annotations

import json
import sys


def _event_payload(chunk: str):
    if not isinstance(chunk, str) or not chunk.startswith("event: activity\n"):
        return None
    try:
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        return json.loads(data_line[6:])
    except Exception:
        return None


def _activity(activity_stream_module, step: str, state: str, message: str):
    return activity_stream_module.sse(
        "activity",
        {"step": step, "state": state, "message": message},
    )


def install(activity_stream_module) -> None:
    raw = activity_stream_module.managed_request_stream
    if getattr(raw, "_ai_workspace_reasoning_trace", False):
        return

    def managed_request_stream(*args, **kwargs):
        reasoning_started = False
        reasoning_completed = False
        for chunk in raw(*args, **kwargs):
            payload = _event_payload(chunk)
            if payload and payload.get("step") == "answer" and payload.get("state") == "running":
                if not reasoning_started:
                    reasoning_started = True
                    yield _activity(
                        activity_stream_module,
                        "reasoning",
                        "running",
                        "Сопоставляю контекст, источники и ограничения ответа…",
                    )
                # Keep the public answer stage, but only after the safe reasoning
                # summary has been surfaced. This is an execution trace, not hidden
                # chain-of-thought or model scratchpad content.
                yield chunk
                continue
            if (
                reasoning_started
                and not reasoning_completed
                and isinstance(chunk, str)
                and chunk.startswith("event: delta\n")
            ):
                reasoning_completed = True
                yield _activity(
                    activity_stream_module,
                    "reasoning",
                    "completed",
                    "Основания ответа проверены. Формулирую результат.",
                )
            yield chunk

    managed_request_stream._ai_workspace_reasoning_trace = True
    managed_request_stream._raw_managed_request_stream = raw
    activity_stream_module.managed_request_stream = managed_request_stream

    # views may already be imported in management commands/tests. Rebind only the
    # exact symbol from this module; normal Django URL loading will import the patched
    # function later without needing this compatibility branch.
    views = sys.modules.get("apps.chat.views")
    if views is not None:
        views.managed_request_stream = managed_request_stream
