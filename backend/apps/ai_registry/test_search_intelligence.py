from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

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


def test_search_status_reports_redundant_free_pool(monkeypatch):
    monkeypatch.setenv(
        "WEB_SEARCH_BASE_URLS",
        "http://searx-a:8080,http://searx-b:8080",
    )
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)

    status = web_tools.searx_search_status()

    assert status["configured"] is True
    assert status["endpoint_count"] == 2
    assert status["endpoints"] == ["http://searx-a:8080", "http://searx-b:8080"]


def test_second_free_searx_endpoint_is_used_when_first_is_down(monkeypatch):
    monkeypatch.setenv(
        "WEB_SEARCH_BASE_URLS",
        "http://searx-a:8080,http://searx-b:8080",
    )
    monkeypatch.setenv("WEB_SEARCH_TRUSTED_HOSTS", "searx-a,searx-b")
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "results": [
            {
                "title": "Fresh result",
                "url": "https://example.com/fresh",
                "content": "current information",
            }
        ]
    }
    first_error = httpx.ConnectError("first endpoint down")

    with patch(
        "apps.ai_registry.search_intelligence.httpx.get",
        side_effect=[first_error, response],
    ) as get, patch(
        "apps.ai_registry.web_tools._assert_public_http_url",
        return_value=None,
    ):
        results = web_tools._search_searx("query", limit=5)

    assert len(results) == 1
    assert results[0].title == "Fresh result"
    assert get.call_count == 2
    assert get.call_args_list[0].args[0] == "http://searx-a:8080/search"
    assert get.call_args_list[1].args[0] == "http://searx-b:8080/search"
