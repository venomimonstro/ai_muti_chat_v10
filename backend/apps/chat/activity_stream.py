from __future__ import annotations

import logging

from django.core.exceptions import ValidationError

from apps.ai_registry.models import AIModel

from .live_tools import needs_web_search
from .managed_stream import managed_run
from .streaming import prepare, sse

logger = logging.getLogger(__name__)


def _activity(step: str, state: str, message: str, **extra):
    return sse(
        "activity",
        {
            "step": step,
            "state": state,
            "message": message,
            **extra,
        },
    )


def managed_request_stream(*, user, conversation, idempotency_key: str, payload: dict):
    """Open SSE immediately and expose verifiable work stages, never chain-of-thought.

    ``prepare`` remains the authoritative transaction for routing/context/billing.
    Running it lazily after the first SSE yield removes the previous silent wait
    without duplicating or weakening any money/reliability logic.
    """
    content = str(payload.get("content") or "")
    search_expected = needs_web_search(content)

    yield _activity(
        "routing",
        "running",
        "Определяю тип задачи и выбираю подходящий маршрут…",
    )
    if search_expected:
        yield _activity(
            "search",
            "running",
            "Проверяю актуальную информацию в интернете…",
        )

    try:
        generation, _created = prepare(
            user=user,
            conversation=conversation,
            idempotency_key=idempotency_key,
            **payload,
        )
    except (ValidationError, AIModel.DoesNotExist) as exc:
        message = " ".join(getattr(exc, "messages", []) or [str(exc)])
        yield _activity("routing", "failed", "Не удалось подобрать доступный маршрут.")
        yield sse(
            "error",
            {
                "code": "preflight_failed",
                "message": message or "Не удалось подготовить запрос. Средства не списаны.",
            },
        )
        return

    context = generation.context_snapshot or {}
    routing = context.get("routing") or {}
    signals = {}
    try:
        signals = generation.routing_decision.signals or {}
    except Exception:
        signals = {}
    tier = {
        "economy": "Простой",
        "balanced": "Средний",
        "maximum": "Сложный",
        "auto": "AUTO",
        "manual": "Выбранная модель",
    }.get(str(routing.get("mode") or ""), "AUTO")
    complexity = signals.get("complexity_score")
    yield _activity(
        "routing",
        "completed",
        f"Маршрут готов · {tier}.",
        taxonomy=routing.get("task_taxonomy"),
        complexity=complexity,
    )

    web = context.get("web_search") or {}
    sources = context.get("web_sources") or []
    if web.get("used"):
        yield _activity(
            "search",
            "completed",
            f"Нашёл актуальные источники: {len(sources)}. Сопоставляю данные…",
            source_count=len(sources),
        )
    elif web.get("required") and web.get("error"):
        yield _activity(
            "search",
            "warning",
            "Не удалось проверить актуальные источники. Ответ не будет выдавать непроверенные свежие данные за факт.",
            error=str(web.get("error"))[:180],
        )
    elif search_expected:
        yield _activity(
            "search",
            "completed",
            "Проверка актуальности завершена.",
            source_count=0,
        )

    if context.get("attached_files"):
        yield _activity(
            "context",
            "completed",
            f"Подготовил материалы из файлов: {len(context.get('attached_files') or [])}.",
        )

    yield _activity(
        "answer",
        "running",
        "Формирую ответ…",
    )

    answer_started = False
    completed = False
    try:
        for chunk in managed_run(generation):
            if not answer_started and isinstance(chunk, str) and chunk.startswith("event: delta\n"):
                answer_started = True
                yield _activity("answer", "streaming", "Ответ готовится и уже поступает…")
            if isinstance(chunk, str) and chunk.startswith("event: completed\n"):
                completed = True
            yield chunk
    except GeneratorExit:
        raise
    except BaseException:
        logger.exception("Chat activity stream failed generation_id=%s", generation.id)
        raise
    else:
        if completed:
            yield _activity("answer", "completed", "Готово.")
