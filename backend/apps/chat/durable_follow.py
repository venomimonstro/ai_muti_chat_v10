from __future__ import annotations

import os
import time

from .models import Generation
from .streaming import sse

FOLLOW_POLL_SECONDS = max(0.2, float(os.getenv("CHAT_RECONNECT_POLL_SECONDS", "0.5")))
FOLLOW_HEARTBEAT_SECONDS = max(1.0, float(os.getenv("CHAT_RECONNECT_HEARTBEAT_SECONDS", "5")))


def _snapshot(generation_id):
    row = (
        Generation.objects.select_related("assistant_message")
        .only(
            "id",
            "state",
            "error_code",
            "actual_cost_rub",
            "assistant_message__content",
            "assistant_message__status",
        )
        .get(pk=generation_id)
    )
    return {
        "state": row.state,
        "error_code": row.error_code,
        "cost_rub": str(row.actual_cost_rub or 0),
        "text": row.assistant_message.content,
    }


def follow_existing_generation(generation):
    """Follow a durable generation without starting a second provider request.

    Replayed idempotent requests, browser refreshes and a second tab must attach to
    the existing Generation. The follower never mutates state, reservations or
    provider health; the original producer (or stale recovery) remains authoritative.
    """
    last_text = None
    heartbeat_at = 0.0
    while True:
        row = _snapshot(generation.id)
        state = row["state"]
        text = row["text"] or ""

        if text and text != last_text:
            last_text = text
            yield sse(
                "snapshot",
                {
                    "text": text,
                    "state": state,
                    "cost_rub": row["cost_rub"],
                    "reconnected": True,
                },
            )

        if state == Generation.State.COMPLETED:
            if not text:
                yield sse(
                    "snapshot",
                    {
                        "text": "",
                        "state": state,
                        "cost_rub": row["cost_rub"],
                        "reconnected": True,
                    },
                )
            yield sse(
                "completed",
                {
                    "generation_id": str(generation.id),
                    "state": state,
                    "cost_rub": row["cost_rub"],
                    "reconnected": True,
                },
            )
            return

        if state == Generation.State.CANCELLED:
            yield sse(
                "cancelled",
                {
                    "generation_id": str(generation.id),
                    "state": state,
                    "partial": bool(text),
                    "cost_rub": row["cost_rub"],
                    "reconnected": True,
                    "message": (
                        "Генерация остановлена. Списана только подтверждённая стоимость уже полученной части ответа."
                        if row["cost_rub"] not in {"0", "0.0000", "0.00"}
                        else "Запрос остановлен. Неподтверждённые расходы не списаны."
                    ),
                },
            )
            return

        if state == Generation.State.FAILED:
            yield sse(
                "error",
                {
                    "code": "AI-103",
                    "support_code": row["error_code"] or "AI-103",
                    "partial": bool(text),
                    "cost_rub": row["cost_rub"],
                    "reconnected": True,
                    "message": (
                        "Ответ был прерван и сохранён. Списана только подтверждённая стоимость; повторите запрос."
                        if text
                        else "Не удалось завершить предыдущий запрос. Неподтверждённые расходы не списаны; повторите запрос."
                    ),
                },
            )
            return

        now = time.monotonic()
        if now >= heartbeat_at:
            yield sse(
                "heartbeat",
                {
                    "generation_id": str(generation.id),
                    "state": state,
                    "reconnected": True,
                },
            )
            heartbeat_at = now + FOLLOW_HEARTBEAT_SECONDS
        time.sleep(FOLLOW_POLL_SECONDS)
