from __future__ import annotations

import json
import logging

logger = logging.getLogger("chat.pipeline")


def _safe(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return str(value)
    except Exception:
        return type(value).__name__


def trace(stage: str, *, generation=None, correlation_id=None, **fields) -> None:
    """Structured, secret-free production markers for one customer chat request."""
    payload = {"stage": str(stage)}
    if generation is not None:
        payload["generation_id"] = str(getattr(generation, "id", "") or "")
        payload["correlation_id"] = str(
            getattr(generation, "correlation_id", "") or correlation_id or ""
        )
    elif correlation_id:
        payload["correlation_id"] = str(correlation_id)
    for key, value in fields.items():
        if key.casefold() in {"secret", "api_key", "authorization", "token", "password"}:
            continue
        if isinstance(value, (list, tuple, set)):
            payload[key] = [_safe(item) for item in list(value)[:50]]
        elif isinstance(value, dict):
            payload[key] = {
                str(k): _safe(v)
                for k, v in list(value.items())[:50]
                if str(k).casefold()
                not in {"secret", "api_key", "authorization", "token", "password"}
            }
        else:
            payload[key] = _safe(value)
    logger.info("[CHAT_PIPELINE] %s", json.dumps(payload, ensure_ascii=False, default=str))
