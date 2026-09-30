from __future__ import annotations

import json
import sys


def _parse_error(chunk):
    if not isinstance(chunk, str) or not chunk.startswith("event: error"):
        return None
    for line in chunk.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            value = json.loads(line[5:].strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
    return None


def install(streaming_module) -> None:
    """Keep user-visible failure text aligned with actual billing semantics.

    A stream that already delivered text is materially different from a request
    that failed before any output. The generic provider message historically said
    "money was not charged" for both cases, which can be false when upstream usage
    was already confirmed and settled by the billing recovery hook.
    """
    raw_run = streaming_module.run
    if getattr(raw_run, "_ai_workspace_error_contract", False):
        return

    def run(generation, *args, **kwargs):
        for chunk in raw_run(generation, *args, **kwargs):
            payload = _parse_error(chunk)
            if payload is not None and bool(payload.get("partial")):
                upstream_code = str(payload.get("code") or "")
                payload["cause_code"] = upstream_code
                payload["code"] = "partial_response_interrupted"
                payload["message"] = (
                    "Ответ прервался после получения части текста. Полученная часть сохранена. "
                    "Если провайдер подтвердил расход, списана только подтверждённая стоимость; "
                    "неподтверждённая часть резерва возвращена."
                )
                yield streaming_module.sse("error", payload)
                continue
            yield chunk

    run._ai_workspace_error_contract = True
    run._raw_run = raw_run
    streaming_module.run = run

    for module_name in ("apps.chat.managed_stream", "apps.chat.services", "apps.chat.views"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "run"):
            module.run = run
