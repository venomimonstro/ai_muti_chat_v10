from __future__ import annotations

import os
import re
from decimal import Decimal

from apps.evals.models import EvalCase


# AUTO Router v3 deliberately keeps classification local and deterministic. A
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

# If two rule groups have the same hit count, choose by product semantics, never
# by lexicographical ordering of taxonomy strings.
INTENT_PRIORITY = (
    EvalCase.Taxonomy.DEBUGGING,
    EvalCase.Taxonomy.CODING,
    EvalCase.Taxonomy.SPREADSHEETS,
    EvalCase.Taxonomy.RESEARCH,
    EvalCase.Taxonomy.SEO,
    EvalCase.Taxonomy.MARKETING,
    EvalCase.Taxonomy.TRANSLATION,
    EvalCase.Taxonomy.EXTRACTION,
    EvalCase.Taxonomy.STRUCTURING,
    EvalCase.Taxonomy.COPYWRITING,
    EvalCase.Taxonomy.EDITING,
    EvalCase.Taxonomy.REASONING,
    EvalCase.Taxonomy.RUSSIAN_STYLE,
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
    # A simple factual "почему" must not automatically buy the maximum tier.
    EvalCase.Taxonomy.REASONING: 0.48,
    EvalCase.Taxonomy.LONG_DOCUMENTS: 0.74,
}


def _has_any(text: str, markers) -> bool:
    return any(marker in text for marker in markers)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


def _resolved_intent(router_module, text: str, fallback: str):
    hits = []
    priority = {str(value): index for index, value in enumerate(INTENT_PRIORITY)}
    for taxonomy, needles in router_module.RULES:
        count = sum(str(needle).casefold() in text for needle in needles)
        if count:
            hits.append((count, str(taxonomy)))
    if not hits:
        return fallback, []
    hits.sort(key=lambda item: (-item[0], priority.get(item[1], 10_000), item[1]))
    return hits[0][1], hits[:3]


def _complexity(base, text: str, taxonomy: str) -> tuple[float, dict]:
    signals = dict(base.signals or {})
    tokens = int(signals.get("content_tokens") or 0)

    # The legacy classifier treats the word "api" as coding. A conceptual
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
    # Vision is a capability requirement first, not a reason to buy Max by
    # itself. A small uplift covers the additional interpretation burden while
    # tier continuity finds the first pool containing a healthy vision model.
    if signals.get("needs_vision"):
        score += 0.06
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

    # Freshness/search itself is intentionally not a complexity upgrade. It is
    # recorded as a tool requirement and handled by the web pipeline.
    score = _bounded(score)
    return score, {
        **signals,
        "needs_tools": needs_freshness,
        "needs_freshness": needs_freshness,
        "complexity_score": round(score, 4),
        "complexity_version": "router-v3.2",
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
    raw_select_route = router_module.select_route

    def classify_task(content, conversation):
        base = raw_classify(content, conversation)
        normalized = re.sub(r"\s+", " ", str(content or "").casefold()).strip()
        taxonomy, matched = _resolved_intent(router_module, normalized, str(base.taxonomy))
        score, signals = _complexity(base, normalized, taxonomy)
        taxonomy = signals.get("resolved_taxonomy") or taxonomy
        confidence = float(base.confidence)
        if matched:
            confidence = min(0.98, 0.58 + float(matched[0][0]) * 0.12)
        if signals.get("conceptual_api"):
            confidence = max(0.72, confidence)
        signals["matched_rules"] = matched
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
        simple_max = float(os.getenv("AUTO_ROUTER_SIMPLE_MAX", "0.28"))
        complex_min = float(os.getenv("AUTO_ROUTER_COMPLEX_MIN", "0.68"))
        hard_context_tokens = max(2500, int(os.getenv("AUTO_ROUTER_HARD_CONTEXT_TOKENS", "5000")))

        # Capability requirements (vision/web/files) are resolved independently
        # by candidate filtering and tier continuity. They must not force an
        # expensive tier unless the reasoning/context complexity also warrants it.
        if tokens >= hard_context_tokens or score >= complex_min:
            return "maximum"
        if score <= simple_max and tokens < 700 and not signals.get("has_project_files"):
            return "economy"
        return "balanced"

    # Patch classification before raw_select_route is called: the base router
    # resolves globals dynamically and therefore uses these v3 functions.
    classify_task._ai_workspace_router_v3 = True
    classify_task._raw_classify_task = raw_classify
    router_module.classify_task = classify_task
    router_module._auto_tier = auto_tier

    def select_route(*, conversation, content):
        route = raw_select_route(conversation=conversation, content=content)
        if conversation.routing_mode == "manual":
            return route

        eligible = [
            dict(item)
            for item in route.candidates
            if item.get("status") == "eligible" and item.get("score") is not None
        ]
        if len(eligible) <= 1:
            return route

        tier = (
            auto_tier(route.classification)
            if conversation.routing_mode == "auto"
            else conversation.routing_mode
        )
        pool = router_module._tier_pool(route.policy.thresholds or {}, tier)
        pool_rank = {slug: index for index, slug in enumerate(pool)}
        # Admin membership is a hard constraint. Priority is only a stable
        # tie-breaker; request-specific quality/cost/latency/health score wins.
        eligible.sort(
            key=lambda item: (
                -float(item.get("score") or 0),
                pool_rank.get(str(item.get("model") or ""), 10_000),
                str(item.get("model") or ""),
            )
        )
        selected_row = eligible[0]
        selected_slug = str(selected_row["model"])

        models = {
            item.slug: item
            for item in router_module.AIModel.objects.filter(
                slug__in=[str(row["model"]) for row in eligible], enabled=True
            ).select_related("provider", "current_version")
        }
        selected = models.get(selected_slug)
        if selected is None:
            return route

        selected_cost = Decimal(str(selected_row["estimated_cost_rub"]))
        multiplier = Decimal(
            str((route.policy.thresholds or {}).get("fallback_price_multiplier", 1.5))
        )
        allowed_slugs = []
        by_slug = {}
        for rank, item in enumerate(eligible, 1):
            slug = str(item["model"])
            allowed = rank == 1 or Decimal(str(item["estimated_cost_rub"])) <= selected_cost * multiplier
            item["rank"] = rank
            item["fallback_allowed"] = allowed
            by_slug[slug] = item
            if allowed and slug in models:
                allowed_slugs.append(slug)

        candidates = [by_slug.get(str(item.get("model") or ""), item) for item in route.candidates]
        task_label = router_module.TASK_LABELS.get(
            route.classification.taxonomy, route.classification.taxonomy
        )
        tier_label = router_module.MODE_LABELS.get(tier, tier)
        if conversation.routing_mode == "auto":
            explanation = (
                f"AUTO определил уровень «{tier_label}» для задачи «{task_label}» и выбрал "
                f"{selected.display_name} по качеству, стоимости, скорости и доступности; "
                "при сбое будет использована следующая допустимая модель."
            )
        else:
            explanation = (
                f"Уровень «{tier_label}»: выбрана {selected.display_name} по качеству, стоимости, "
                "скорости и доступности; при сбое будет использована резервная модель."
            )

        return router_module.RouteSelection(
            policy=route.policy,
            classification=route.classification,
            selected=selected,
            ordered_models=[models[slug] for slug in allowed_slugs],
            candidates=candidates,
            explanation=explanation,
            estimated_input_tokens=route.estimated_input_tokens,
            estimated_output_tokens=min(router_module.OUTPUT_TOKENS, selected.max_output_tokens),
            estimated_cost_rub=selected_cost,
        )

    select_route._ai_workspace_router_v3 = True
    select_route._raw_select_route = raw_select_route
    router_module.select_route = select_route
