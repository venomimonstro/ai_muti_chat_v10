from types import SimpleNamespace

from apps.ai_registry import router


def _conversation():
    return SimpleNamespace(project_id=None)


def _route(text):
    classification = router.classify_task(text, _conversation())
    return classification, router._auto_tier(classification)


def test_advanced_math_analysis_uses_complex_pool():
    classification, tier = _route(
        "Проанализируй нелинейную оптимизацию, обоснуй решение и проверь производную целевой функции"
    )
    assert classification.signals["advanced_math_signals"] >= 2
    assert classification.signals["complexity_score"] >= 0.68
    assert tier == "maximum"


def test_legal_risk_analysis_uses_complex_pool():
    classification, tier = _route(
        "Проанализируй договор поставки, оцени юридические риски и сравни сценарии для покупателя"
    )
    assert classification.signals["high_stakes_domain_signals"] >= 1
    assert classification.signals["analysis_signals"] >= 2
    assert tier == "maximum"


def test_multi_constraint_business_problem_uses_complex_pool():
    classification, tier = _route(
        "Выбери оптимальную стратегию с учётом бюджета, срока, трёх критериев и ограничения по марже одновременно"
    )
    assert classification.signals["constraint_signals"] >= 3
    assert tier == "maximum"


def test_current_tax_fact_is_not_upgraded_just_for_domain_and_freshness():
    classification, tier = _route("Какая сейчас ставка НДС в России?")
    assert classification.signals["needs_freshness"] is True
    assert classification.signals["high_stakes_domain_signals"] >= 1
    assert classification.signals["analysis_signals"] == 0
    assert tier != "maximum"


def test_simple_fact_stays_simple():
    classification, tier = _route("Переведи на английский: Добрый день")
    assert classification.signals["complexity_version"] == "router-v3.3"
    assert tier == "economy"
