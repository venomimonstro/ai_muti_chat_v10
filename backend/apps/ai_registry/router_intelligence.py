from __future__ import annotations

import re

from apps.evals.models import EvalCase


# AUTO Router v3 deliberately keeps classification local and deterministic.  A
# second LLM call here would add latency/cost and would itself need routing.
# Search/freshness is a separate axis from reasoning complexity: a current FX
# quote may need the web but does not need the most expensive reasoning model.

SIMPLE_INTENTS = (
    "переведи",
    "перевод",
    "исправь текст",
    "проверь орфограф",
    "сократи",
    "перефраз",
    "извлеки",
    "вытащи",
    "выпиши",
)

HARD_REASONING_MARKERS = (
    "проведи аудит",
    "детальный аудит",
    "глубокий анализ",
    "проанализируй архитектур",
    "спроектируй архитектур",
    "найди уязвим",
    "race condition",
    "traceback",
    "отлад",
    "докажи",
    "обоснуй",
    "рассчитай сценар",
    "финансовая модель",
    "стратегия",
    "сравни варианты",
    "пошагово",
    "несколько гипотез",
    "root cause",
)

MULTISTEP_MARKERS = (
    "сначала",
    "затем",
    "после этого",
    "поэтап",
    "пошаг",
    "сравни",
    "проверь",
    "аудит",
    "архитектур",
    "план разработки",
    "roadmap",
    "роадмап",
)

CODE_ACTION_MARKERS = (
    "напиши код",
    "реализуй",
    "исправь код",
    "рефактор",
    "debug",
    "отлад",
    "traceback",
    "stack trace",
    "sql запрос",
    "python код",
    "javascript код",
    "typescript",
)

FRESHNESS_MARKERS = (
    "сегодня",
    "сейчас",
    "актуальн",
    "последние новости",
    "новости",
    "последняя версия",
    "текущая версия",
    "курс",
    "цена",
    "стоимость",
    "расписание",
    "тариф",
    "закон",
    "кто сейчас",
    "найди в интернете",
    "проверь в интернете",
    "найди источник",
)

BASE_COMPLEXITY = {
    EvalCase.Taxonomy.QA: 0.25,
    EvalCase.Taxonomy.TRANSLATION: 0.14,
    EvalCase.Taxonomy.EXTRACTION: 0.18,
    EvalCase.Taxonomy.EDITING: 0.20,
    EvalCase.Taxonomy.RUSSIAN_STYLE: 0.20,
    EvalCase.Taxonomy.STRUCTURING: 0.34,
    EvalCase.Taxonomy.COPYWRITING: 0.36,
    EvalCase.Taxonomy.SEO: 0.42,
    EvalCase.Taxonomy.MARKETING: 0.48,
    EvalCase.Taxonomy.CODING: 0.54,
    EvalCase.Taxonomy.DEBUGGING: 0.66,
    EvalCase.Taxonomy.SPREADSHEETS: 0.53,
    EvalCase.Taxonomy.RESEARCH: 0.58,
    EvalCase.Taxonomy.REASONING: 0.60,
    EvalCase.Taxonomy.LONG_DOCUMENTS: 0.74,
}


def _has_any(text: str, markers) -> bool:
    return any(marker in text for marker in markers)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


def _complexity(base, text: str) -> tuple[float, dict]:
    signals = dict(base.signals or {})
    tokens = int(signals.get("content_tokens") or 0)
    taxonomy = base.taxonomy

    # The legacy classifier treats the word "api" as coding.  A conceptual
    # question such as "что такое REST API" is ordinary Q&A unless the user asks
    # us to implement/debug something.
    coding_action = _has_any(text, CODE_ACTION_MARKERS)
    conceptual_api = (
        taxonomy == EvalCase.Taxonomy.CODING
        and "api" in text
        and not coding_action
        and _has_any(text, ("что такое", "объясни", "простыми словами", "чем отличается"))
    )
    if conceptual_api:
        taxonomy = EvalCase.Taxonomy.QA

    score = BASE_COMPLEXITY.get(taxonomy, 0.40)
    hard_reasoning = _has_any(text, HARD_REASONING_MARKERS)
    multistep_hits = sum(marker in text for marker in MULTISTEP_MARKERS)
    simple_intent = _has_any(text, SIMPLE_INTENTS)
    needs_freshness = bool(signals.get("needs_tools")) or _has_any(text, FRESHNESS_MARKERS)

    if hard_reasoning:
        score += 0.22
    if multistep_hits >= 2:
        score += 0.12
    elif multistep_hits == 1:
        score += 0.05
    if signals.get("long_context"):
        score += 0.22
    if signals.get("needs_vision"):
        score += 0.12
    if signals.get("has_project_files"):
        score += 0.07
    if tokens >= 2500:
        score += 0.20
    elif tokens >= 1200:
        score += 0.10
    elif tokens >= 700:
        score += 0.04
    if simple_intent and tokens < 700 and not hard_reasoning:
        score -= 0.10

    # Freshness/search itself is intentionally not a complexity upgrade.  It is
    # recorded as a tool requirement and handled by the web pipeline.
    score = _bounded(score)
    return score, {
        **signals,
        "needs_tools": needs_freshness,
        "needs_freshness": needs_freshness,
        "complexity_score": round(score, 4),
        "complexity_version": "router-v3",
        "hard_reasoning": hard_reasoning,
        "multistep_signals": multistep_hits,
        "coding_action": coding_action,
        "conceptual_api": conceptual_api,
        "resolved_taxonomy": taxonomy,
    }


def install(router_module) -> None:
    if getattr(router_module.classify_task, "_ai_workspace_router_v3", False):
        return

    raw_classify = router_module.classify_task

    def classify_task(content, conversation):
        base = raw_classify(content, conversation)
        normalized = re.sub(r"\s+", " ", str(content or "").casefold()).strip()
        score, signals = _complexity(base, normalized)
        taxonomy = signals.get("resolved_taxonomy") or base.taxonomy
        confidence = float(base.confidence)
        if signals.get("conceptual_api"):
            confidence = max(0.72, confidence)
        return router_module.TaskClassification(
            taxonomy=taxonomy,
            confidence=min(0.99, confidence),
            required_capabilities=list(base.required_capabilities),
            signals=signals,
        )

    def auto_tier(classification):
        signals = classification.signals or {}
        score = float(signals.get("complexity_score", 0.45))
        tokens = int(signals.get("content_tokens") or 0)

        if (
            signals.get("long_context")
            or signals.get("needs_vision")
            or tokens >= 2500
            or score >= 0.68
        ):
            return "maximum"
        if (
            score <= 0.28
            and tokens < 700
            and not signals.get("has_project_files")
            and not signals.get("needs_vision")
        ):
            return "economy"
        return "balanced"

    classify_task._ai_workspace_router_v3 = True
    classify_task._raw_classify_task = raw_classify
    router_module.classify_task = classify_task
    router_module._auto_tier = auto_tier
