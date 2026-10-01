from types import SimpleNamespace

from apps.ai_registry import router
from apps.evals.models import EvalCase


def _conversation():
    return SimpleNamespace(project_id=None)


def _classify(text):
    result = router.classify_task(text, _conversation())
    return result, router._auto_tier(result)


def test_current_exchange_rate_needs_search_but_can_use_simple_pool():
    classification, tier = _classify("Какой сейчас курс доллара к рублю?")
    assert classification.signals["needs_freshness"] is True
    assert classification.signals["complexity_score"] <= 0.28
    assert tier == "economy"


def test_conceptual_api_question_is_not_treated_as_coding():
    classification, tier = _classify("Что такое REST API простыми словами?")
    assert classification.taxonomy == EvalCase.Taxonomy.QA
    assert classification.signals["conceptual_api"] is True
    assert tier == "economy"


def test_short_translation_uses_simple_pool():
    classification, tier = _classify("Переведи на английский: Доброе утро")
    assert classification.taxonomy == EvalCase.Taxonomy.TRANSLATION
    assert tier == "economy"


def test_short_implementation_request_uses_balanced_pool():
    classification, tier = _classify(
        "Напиши функцию Python, которая сортирует список по дате"
    )
    assert classification.taxonomy == EvalCase.Taxonomy.CODING
    assert tier == "balanced"


def test_deep_debug_audit_uses_complex_pool():
    classification, tier = _classify(
        "Проведи аудит Django race condition, найди root cause и предложи пошаговое исправление кода"
    )
    assert classification.signals["hard_reasoning"] is True
    assert classification.signals["complexity_score"] >= 0.68
    assert tier == "maximum"


def test_search_requirement_does_not_upgrade_simple_reasoning_by_itself():
    classification, tier = _classify("Найди актуальную цену iPhone сегодня")
    assert classification.signals["needs_freshness"] is True
    assert tier == "economy"


def test_simple_why_question_stays_balanced():
    classification, tier = _classify("Почему небо голубое?")
    assert classification.taxonomy == EvalCase.Taxonomy.REASONING
    assert classification.signals["complexity_score"] < 0.68
    assert tier == "balanced"


def test_equal_rule_hits_have_deterministic_debug_priority():
    classification, _tier = _classify("Напиши код Python API и исправь ошибку traceback")
    assert classification.taxonomy == EvalCase.Taxonomy.DEBUGGING
    assert classification.signals["matched_rules"]
    assert classification.signals["complexity_version"] == "router-v3.2"


def test_vision_capability_alone_does_not_force_maximum():
    classification = router.TaskClassification(
        taxonomy=EvalCase.Taxonomy.QA,
        confidence=0.9,
        required_capabilities=["text", "vision"],
        signals={
            "content_tokens": 80,
            "complexity_score": 0.31,
            "needs_vision": True,
            "has_project_files": False,
        },
    )
    assert router._auto_tier(classification) == "balanced"


def test_medium_context_is_not_hard_promoted_but_huge_context_is(monkeypatch):
    monkeypatch.setenv("AUTO_ROUTER_HARD_CONTEXT_TOKENS", "5000")
    medium_context = router.TaskClassification(
        taxonomy=EvalCase.Taxonomy.QA,
        confidence=0.8,
        required_capabilities=["text"],
        signals={"content_tokens": 3000, "complexity_score": 0.55},
    )
    huge_context = router.TaskClassification(
        taxonomy=EvalCase.Taxonomy.QA,
        confidence=0.8,
        required_capabilities=["text"],
        signals={"content_tokens": 5000, "complexity_score": 0.55},
    )
    assert router._auto_tier(medium_context) == "balanced"
    assert router._auto_tier(huge_context) == "maximum"


def test_large_prompt_is_complex_even_without_magic_keywords():
    text = "Проанализируй данные и найди противоречия. " + ("Факт для проверки. " * 4000)
    classification, tier = _classify(text)
    assert classification.signals["content_tokens"] >= 2500
    assert tier == "maximum"
