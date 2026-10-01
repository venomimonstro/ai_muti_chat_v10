from __future__ import annotations

from dataclasses import replace
from decimal import Decimal


# Deterministic intent priority. A tie between e.g. "code" and "debug" must not
# depend on the lexical value of a taxonomy enum.
INTENT_PRIORITY = (
    "debugging",
    "coding",
    "spreadsheets",
    "research",
    "seo",
    "marketing",
    "translation",
    "extraction",
    "structuring",
    "copywriting",
    "editing",
    "reasoning",
    "russian_style",
)

BASE_COMPLEXITY = {
    "translation": 0.18,
    "extraction": 0.22,
    "editing": 0.22,
    "russian_style": 0.22,
    "qa": 0.38,
    "copywriting": 0.40,
    "structuring": 0.42,
    "seo": 0.45,
    "marketing": 0.46,
    "reasoning": 0.48,
    "coding": 0.52,
    "spreadsheets": 0.54,
    "debugging": 0.56,
    "research": 0.56,
    "long_documents": 0.72,
}

STRONG_COMPLEXITY_MARKERS = (
    "глубокий анализ",
    "детальный анализ",
    "детально проанализ",
    "проведи аудит",
    "архитектур",
    "спроектируй",
    "многошаг",
    "поэтапно",
    "найди причины",
    "сравни несколько",
    "сравни источники",
    "с доказательств",
    "проверь гипотез",
    "оптимизируй",
    "рефактор",
    "production",
    "продакш",
)

SIMPLE_REQUEST_MARKERS = (
    "кратко",
    "одним предложением",
    "в двух словах",
    "исправь опечат",
)


def _deterministic_taxonomy(router_module, content: str, fallback: str):
    normalized = str(content or "").casefold()
    hits_by_taxonomy: dict[str, int] = {}
    for taxonomy, needles in router_module.RULES:
        hits = sum(str(needle).casefold() in normalized for needle in needles)
        if hits:
            hits_by_taxonomy[str(taxonomy)] = hits
    if not hits_by_taxonomy:
        return fallback, []

    priority = {name: index for index, name in enumerate(INTENT_PRIORITY)}
    ordered = sorted(
        hits_by_taxonomy.items(),
        key=lambda item: (-item[1], priority.get(item[0], 10_000), item[0]),
    )
    return ordered[0][0], ordered[:3]


def _complexity_score(taxonomy: str, signals: dict, content: str):
    score = float(BASE_COMPLEXITY.get(str(taxonomy), 0.40))
    reasons = [f"intent:{taxonomy}"]
    tokens = int(signals.get("content_tokens") or 0)
    text = str(content or "").casefold()

    if tokens >= 3000:
        score += 0.20
        reasons.append("very_long_input")
    elif tokens >= 1500:
        score += 0.12
        reasons.append("long_input")
    elif tokens >= 700:
        score += 0.06
        reasons.append("medium_input")

    if signals.get("long_context"):
        score += 0.12
        reasons.append("long_context")
    if signals.get("needs_vision"):
        score += 0.08
        reasons.append("vision")

    # Freshness is a tool requirement, not proof that the language/reasoning task
    # itself needs the most expensive model. Search quality is handled separately.
    if signals.get("needs_tools"):
        score += 0.04
        reasons.append("fresh_data")

    strong_hits = [marker for marker in STRONG_COMPLEXITY_MARKERS if marker in text]
    if strong_hits:
        score += min(0.18, 0.10 + 0.03 * (len(strong_hits) - 1))
        reasons.append("multi_step_or_deep")

    if any(marker in text for marker in SIMPLE_REQUEST_MARKERS) and tokens < 700:
        score -= 0.06
        reasons.append("explicitly_brief")

    score = max(0.0, min(score, 1.0))
    return round(score, 4), reasons


def install(router_module) -> None:
    if getattr(router_module, "_ai_workspace_routing_quality_v3", False):
        return

    raw_classify = router_module.classify_task
    raw_select_route = router_module.select_route

    def classify_task(content, conversation):
        raw = raw_classify(content, conversation)
        taxonomy, matched = _deterministic_taxonomy(
            router_module, content, str(raw.taxonomy)
        )
        score, reasons = _complexity_score(taxonomy, raw.signals, content)
        signals = dict(raw.signals or {})
        signals.update(
            {
                "matched_rules": matched,
                "complexity_score": score,
                "complexity_reasons": reasons,
                "classifier_version": "router-v3",
            }
        )
        confidence = raw.confidence
        if matched:
            confidence = min(0.98, 0.58 + matched[0][1] * 0.12)
        return router_module.TaskClassification(
            taxonomy=taxonomy,
            confidence=confidence,
            required_capabilities=list(raw.required_capabilities),
            signals=signals,
        )

    def auto_tier(classification):
        score = float((classification.signals or {}).get("complexity_score", 0.40))
        if score >= 0.68:
            return "maximum"
        if score <= 0.30:
            return "economy"
        return "balanced"

    router_module.classify_task = classify_task
    router_module._auto_tier = auto_tier

    def select_route(*, conversation, content):
        route = raw_select_route(conversation=conversation, content=content)
        if conversation.routing_mode == "manual":
            return route

        # The base router computes hard eligibility and a normalized score. Admin
        # tier membership is authoritative, but assignment order is only a
        # deterministic tie-breaker: it must not hide a materially better model.
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
        eligible.sort(
            key=lambda item: (
                -float(item.get("score") or 0),
                pool_rank.get(item.get("model"), 10_000),
                str(item.get("model") or ""),
            )
        )

        selected_row = eligible[0]
        if selected_row.get("model") == route.selected.slug:
            return route

        model_slugs = [str(item["model"]) for item in eligible]
        models = {
            item.slug: item
            for item in router_module.AIModel.objects.filter(
                slug__in=model_slugs, enabled=True
            ).select_related("provider", "current_version")
        }
        selected = models.get(str(selected_row["model"]))
        if selected is None:
            return route

        selected_cost = Decimal(str(selected_row["estimated_cost_rub"]))
        multiplier = Decimal(
            str((route.policy.thresholds or {}).get("fallback_price_multiplier", 1.5))
        )
        allowed_slugs = []
        rank_by_slug = {}
        for rank, item in enumerate(eligible, 1):
            slug = str(item["model"])
            allowed = rank == 1 or Decimal(str(item["estimated_cost_rub"])) <= selected_cost * multiplier
            item["rank"] = rank
            item["fallback_allowed"] = allowed
            rank_by_slug[slug] = (rank, allowed)
            if allowed and slug in models:
                allowed_slugs.append(slug)

        candidates = []
        by_slug = {str(item["model"]): item for item in eligible}
        for original in route.candidates:
            slug = str(original.get("model") or "")
            if slug in by_slug:
                candidates.append(by_slug[slug])
            else:
                candidates.append(original)

        task_label = router_module.TASK_LABELS.get(
            route.classification.taxonomy, route.classification.taxonomy
        )
        tier_label = router_module.MODE_LABELS.get(tier, tier)
        explanation = (
            f"AUTO определил уровень «{tier_label}» для задачи «{task_label}» и выбрал "
            f"{selected.display_name} по качеству, стоимости, задержке и доступности; "
            "при сбое будет использована следующая допустимая модель."
            if conversation.routing_mode == "auto"
            else (
                f"Уровень «{tier_label}»: выбрана {selected.display_name} по качеству, "
                "стоимости, задержке и доступности; при сбое будет использована резервная модель."
            )
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

    select_route._ai_workspace_routing_quality_v3 = True
    select_route._raw_select_route = raw_select_route
    router_module.select_route = select_route
    router_module._ai_workspace_routing_quality_v3 = True
