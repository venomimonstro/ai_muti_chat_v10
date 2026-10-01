from __future__ import annotations

import logging

from django.core.exceptions import ValidationError

from apps.ai_registry.models import AIModel

from .durable_follow import follow_existing_generation
from .live_tools import needs_web_search
from .managed_stream import managed_run
from .models import Generation
from .preflight_terminal import classify_preflight_exception
from .streaming import prepare, sse

logger = logging.getLogger(__name__)


PUBLIC_PREFLIGHT_MESSAGES = {
    "preflight_provider_funding": (
        "Сейчас нет AI-канала с доступным закупочным балансом для этого запроса. "
        "Деньги не списаны. Попробуйте ещё раз позже или выберите другую модель."
    ),
    "preflight_balance": "Недостаточно средств на балансе. Деньги не списаны.",
    "preflight_price": (
        "Модель временно недоступна из-за настройки стоимости. Деньги не списаны. "
        "Выберите AUTO или другую модель."
    ),
    "preflight_context": (
        "Запрос не помещается в доступный контекст выбранного AI-канала. "
        "Сократите сообщение или используйте другой уровень/модель. Деньги не списаны."
    ),
    "preflight_spend_guard": (
        "Запрос остановлен лимитом расходов до обращения к AI. Деньги не списаны."
    ),
    "preflight_web": (
        "Не удалось подготовить актуальный поиск для этого запроса. Деньги не списаны. "
        "Повторите запрос позже."
    ),
    "preflight_input": (
        "Не удалось подготовить вложения для AI. Проверьте файл и повторите запрос. Деньги не списаны."
    ),
    "preflight_no_model": (
        "Сейчас нет подходящей доступной модели. Деньги не списаны. "
        "Попробуйте AUTO или повторите запрос позже."
    ),
    "preflight_validation": "Не удалось подготовить запрос. Деньги не списаны. Проверьте параметры и повторите.",
    "preflight_internal": (
        "Не удалось безопасно подготовить запрос из-за внутреннего сбоя. Деньги не списаны. "
        "Повторите запрос."
    ),
}


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


def _status_events(step: str, state: str, message: str, **extra):
    """Structured activity plus a compatibility status for the current UI."""
    yield _activity(step, state, message, **extra)
    yield sse(
        "routing",
        {
            "explanation": message,
            "activity_step": step,
            "activity_state": state,
            **extra,
        },
    )


def _preflight_error_payload(exc: Exception) -> dict:
    code = classify_preflight_exception(exc)
    return {
        "code": "preflight_failed",
        "support_code": code,
        "message": PUBLIC_PREFLIGHT_MESSAGES.get(code, PUBLIC_PREFLIGHT_MESSAGES["preflight_internal"]),
    }


def managed_request_stream(*, user, conversation, idempotency_key: str, payload: dict):
    """Open SSE immediately and expose verifiable work stages, never chain-of-thought.

    ``prepare`` remains the authoritative transaction for routing/context/billing.
    Idempotent reconnects attach to an existing durable Generation instead of
    starting a second provider call or surfacing ``generation_in_progress``.
    """
    content = str(payload.get("content") or "")
    search_expected = needs_web_search(content)

    yield from _status_events(
        "routing",
        "running",
        "Определяю тип задачи и выбираю подходящий маршрут…",
    )
    if search_expected:
        yield from _status_events(
            "search",
            "running",
            "Проверяю актуальную информацию в интернете…",
        )

    try:
        generation, created = prepare(
            user=user,
            conversation=conversation,
            idempotency_key=idempotency_key,
            **payload,
        )
    except GeneratorExit:
        raise
    except Exception as exc:
        if not isinstance(exc, (ValidationError, AIModel.DoesNotExist)):
            logger.exception(
                "Chat preflight failed owner_id=%s conversation_id=%s",
                getattr(user, "pk", None),
                getattr(conversation, "pk", None),
            )
        payload_out = _preflight_error_payload(exc)
        yield from _status_events(
            "routing", "failed", "Не удалось безопасно подготовить запрос."
        )
        yield sse("error", payload_out)
        return

    generation.refresh_from_db(fields=["state"])
    if not created and generation.state != Generation.State.QUEUED:
        yield from _status_events(
            "answer",
            "reconnecting",
            "Продолжаю уже запущенный ответ без повторного запроса к AI…",
            generation_id=str(generation.id),
        )
        yield from follow_existing_generation(generation)
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
    yield from _status_events(
        "routing",
        "completed",
        f"Маршрут готов · {tier}.",
        taxonomy=routing.get("task_taxonomy"),
        complexity=complexity,
    )

    web = context.get("web_search") or {}
    sources = context.get("web_sources") or []
    if web.get("used"):
        yield from _status_events(
            "search",
            "completed",
            f"Нашёл актуальные источники: {len(sources)}. Сопоставляю данные…",
            source_count=len(sources),
        )
    elif web.get("required") and web.get("error"):
        yield from _status_events(
            "search",
            "warning",
            "Не удалось проверить актуальные источники. Ответ не будет выдавать непроверенные свежие данные за факт.",
            error="search_unavailable",
        )
    elif search_expected:
        yield from _status_events(
            "search",
            "completed",
            "Проверка актуальности завершена.",
            source_count=0,
        )

    if context.get("attached_files"):
        yield from _status_events(
            "context",
            "completed",
            f"Подготовил материалы из файлов: {len(context.get('attached_files') or [])}.",
        )

    yield from _status_events(
        "answer",
        "running",
        "Формирую ответ…",
    )

    answer_started = False
    completed = False
    follow_existing = False
    try:
        for chunk in managed_run(generation):
            if isinstance(chunk, str) and "generation_in_progress" in chunk:
                follow_existing = True
                break
            if not answer_started and isinstance(chunk, str) and chunk.startswith("event: delta\n"):
                answer_started = True
                yield from _status_events(
                    "answer", "streaming", "Ответ готовится и уже поступает…"
                )
            if isinstance(chunk, str) and chunk.startswith("event: completed\n"):
                completed = True
            yield chunk
    except GeneratorExit:
        raise
    except BaseException:
        logger.exception("Chat activity stream failed generation_id=%s", generation.id)
        raise
    else:
        if follow_existing:
            yield from _status_events(
                "answer",
                "reconnecting",
                "Подключаюсь к уже выполняющемуся ответу…",
                generation_id=str(generation.id),
            )
            yield from follow_existing_generation(generation)
            return
        if completed:
            yield from _status_events("answer", "completed", "Готово.")
