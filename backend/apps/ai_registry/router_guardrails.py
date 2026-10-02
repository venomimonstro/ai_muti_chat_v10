from __future__ import annotations

import os
import re


ADVANCED_MATH_MARKERS = (
    "интеграл",
    "производн",
    "дифференциальн",
    "байес",
    "теорем",
    "докажи",
    "доказательство",
    "оптимизац",
    "линейное программирование",
    "нелинейн",
    "вероятност",
    "статистическ",
    "математическ модель",
    "алгоритмическ сложност",
    "асимптотик",
)

HIGH_STAKES_DOMAIN_MARKERS = (
    "договор",
    "контракт",
    "юридическ",
    "налог",
    "ндс",
    "судебн",
    "регулятор",
    "инвестиц",
    "портфел",
    "dcf",
    "unit economics",
    "юнит-эконом",
    "финансов",
    "кредит",
)

ANALYSIS_MARKERS = (
    "проанализируй",
    "оцени риски",
    "риски",
    "сценари",
    "сравни варианты",
    "компромисс",
    "trade-off",
    "tradeoff",
    "обоснуй",
    "рекомендац",
    "выбери стратег",
    "оптимальн",
)

CONSTRAINT_MARKERS = (
    "при условии",
    "с учётом",
    "учитывая",
    "не более",
    "не менее",
    "одновременно",
    "ограничен",
    "критер",
    "бюджет",
    "срок",
    "приоритет",
)


def _hits(text: str, markers) -> int:
    return sum(marker in text for marker in markers)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


def install(router_module) -> None:
    """Add semantic edge-case guardrails on top of Router v3.

    The base router remains responsible for taxonomy, capability filtering and
    model scoring. This layer only adjusts the reasoning-complexity score for
    requests that are easy to underestimate with keyword taxonomy alone.
    Freshness/search is intentionally not an upgrade signal.
    """

    current_classify = router_module.classify_task
    if getattr(current_classify, "_ai_workspace_router_guardrails", False) is True:
        return
    current_auto_tier = router_module._auto_tier

    def classify_task(content, conversation):
        result = current_classify(content, conversation)
        text = re.sub(r"\s+", " ", str(content or "").casefold()).strip()
        signals = dict(result.signals or {})
        score = float(signals.get("complexity_score", 0.45))

        math_hits = _hits(text, ADVANCED_MATH_MARKERS)
        high_stakes_hits = _hits(text, HIGH_STAKES_DOMAIN_MARKERS)
        analysis_hits = _hits(text, ANALYSIS_MARKERS)
        constraint_hits = _hits(text, CONSTRAINT_MARKERS)

        if math_hits >= 2:
            score += 0.24
        elif math_hits == 1 and analysis_hits:
            score += 0.14

        # A simple current fact such as "какая ставка НДС сейчас" must stay cheap:
        # high-stakes domains are promoted only when the user also requests
        # analysis/risk/scenario work.
        if high_stakes_hits and analysis_hits:
            score += 0.16
        if high_stakes_hits >= 2 and (analysis_hits or constraint_hits >= 2):
            score += 0.08

        if constraint_hits >= 3:
            score += 0.18
            # A genuinely multi-constraint optimization/strategy problem belongs
            # in the complex pool even when no single domain keyword is dominant.
            score = max(score, 0.70)
        elif constraint_hits == 2 and analysis_hits:
            score += 0.10

        if analysis_hits >= 2:
            score += 0.10

        score = _bounded(score)
        signals.update(
            {
                "complexity_score": round(score, 4),
                "complexity_version": "router-v3.3",
                "advanced_math_signals": math_hits,
                "high_stakes_domain_signals": high_stakes_hits,
                "analysis_signals": analysis_hits,
                "constraint_signals": constraint_hits,
            }
        )
        return router_module.TaskClassification(
            taxonomy=result.taxonomy,
            confidence=result.confidence,
            required_capabilities=list(result.required_capabilities),
            signals=signals,
        )

    def auto_tier(classification):
        signals = classification.signals or {}
        score = float(signals.get("complexity_score", 0.45))
        tokens = int(signals.get("content_tokens") or 0)
        simple_max = float(os.getenv("AUTO_ROUTER_SIMPLE_MAX", "0.28"))
        complex_min = float(os.getenv("AUTO_ROUTER_COMPLEX_MIN", "0.68"))
        hard_context_tokens = max(2500, int(os.getenv("AUTO_ROUTER_HARD_CONTEXT_TOKENS", "5000")))
        if tokens >= hard_context_tokens or score >= complex_min:
            return "maximum"
        if score <= simple_max and tokens < 700 and not signals.get("has_project_files"):
            return "economy"
        return "balanced"

    classify_task._ai_workspace_router_guardrails = True
    classify_task._raw_classify_task = current_classify
    router_module.classify_task = classify_task
    router_module._auto_tier = auto_tier
