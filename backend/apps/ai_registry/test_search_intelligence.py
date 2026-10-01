from types import SimpleNamespace

from apps.ai_registry import web_tools
from apps.ai_registry.search_intelligence import _canonical_url, _diversify, _intent


def test_current_news_prefers_day_window_and_news_category():
    intent = _intent("Какие последние новости OpenAI сегодня?")
    assert intent == {"time_range": "day", "category": "news"}


def test_current_version_prefers_week_without_forcing_news_category():
    intent = _intent("Какая последняя версия Django?")
    assert intent == {"time_range": "week", "category": "general"}


def test_search_results_are_diversified_by_domain_and_tracking_is_ignored():
    rows = [
        SimpleNamespace(url="https://example.com/a?utm_source=x"),
        SimpleNamespace(url="https://example.com/a?utm_source=y"),
        SimpleNamespace(url="https://example.com/b"),
        SimpleNamespace(url="https://example.com/c"),
        SimpleNamespace(url="https://other.test/x"),
    ]
    result = _diversify(rows, limit=5, per_domain=2)
    assert [item.url for item in result] == [
        "https://example.com/a?utm_source=x",
        "https://example.com/b",
        "https://other.test/x",
    ]
    assert _canonical_url(rows[0].url) == _canonical_url(rows[1].url)


def test_search_runtime_patch_is_installed_by_django_startup():
    assert getattr(web_tools._search_searx, "_ai_workspace_search_v3", False) is True
