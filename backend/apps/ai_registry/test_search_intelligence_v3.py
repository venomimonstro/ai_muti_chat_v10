import httpx

from apps.ai_registry import search_intelligence, web_tools


def test_paid_search_is_not_used_for_customer_traffic_by_default(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER_ORDER", "searx,yandex")
    monkeypatch.delenv("WEB_SEARCH_PAID_PROVIDERS_ENABLED", raising=False)
    monkeypatch.delenv("WEB_SEARCH_PAID_BILLING_READY", raising=False)
    assert web_tools._search_provider_order() == ["searx"]


def test_paid_search_requires_operator_enable_and_unified_billing(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER_ORDER", "searx,yandex")
    monkeypatch.setenv("WEB_SEARCH_PAID_PROVIDERS_ENABLED", "true")
    monkeypatch.delenv("WEB_SEARCH_PAID_BILLING_READY", raising=False)
    assert web_tools._search_provider_order() == ["searx"]
    monkeypatch.setenv("WEB_SEARCH_PAID_BILLING_READY", "true")
    assert web_tools._search_provider_order() == ["searx", "yandex"]


def test_searx_fails_over_to_second_free_endpoint(monkeypatch):
    monkeypatch.setenv(
        "WEB_SEARCH_BASE_URLS",
        "https://search-one.example,https://search-two.example",
    )
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)
    monkeypatch.setattr(web_tools, "_assert_search_provider_url", lambda _url: None)
    monkeypatch.setattr(web_tools, "_assert_public_http_url", lambda _url: None)
    calls = []

    class Response:
        def __init__(self, url):
            self.url = url
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "url": "https://source.example/current",
                        "title": "Current source",
                        "content": "Fresh result",
                    }
                ]
            }

    def fake_get(url, **_kwargs):
        calls.append(url)
        if "search-one" in url:
            raise httpx.ConnectError("first endpoint down")
        return Response(url)

    monkeypatch.setattr(search_intelligence.httpx, "get", fake_get)
    results = web_tools._search_searx("актуальная информация", limit=5)
    assert [item.title for item in results] == ["Current source"]
    assert any("search-one.example" in value for value in calls)
    assert any("search-two.example" in value for value in calls)


def test_news_search_retries_general_category_before_paid_provider(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_BASE_URLS", "https://search.example")
    monkeypatch.delenv("WEB_SEARCH_BASE_URL", raising=False)
    monkeypatch.setattr(web_tools, "_assert_search_provider_url", lambda _url: None)
    monkeypatch.setattr(web_tools, "_assert_public_http_url", lambda _url: None)
    categories = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            if categories[-1] == "general":
                return {"results": [{"url": "https://source.example/a", "title": "A", "content": "fresh"}]}
            return {"results": []}

    def fake_get(_url, **kwargs):
        categories.append(kwargs["params"]["categories"])
        return Response()

    monkeypatch.setattr(search_intelligence.httpx, "get", fake_get)
    results = web_tools._search_searx("последние новости AI сегодня", limit=5)
    assert [item.title for item in results] == ["A"]
    assert categories[-1] == "general"


def test_search_status_reports_free_redundancy_and_billing_guard(monkeypatch):
    monkeypatch.setenv(
        "WEB_SEARCH_BASE_URLS",
        "https://search-one.example,https://search-two.example",
    )
    monkeypatch.setenv("WEB_SEARCH_PAID_PROVIDERS_ENABLED", "true")
    monkeypatch.delenv("WEB_SEARCH_PAID_BILLING_READY", raising=False)
    status = web_tools.web_search_status()
    assert status["searx"]["endpoint_count"] == 2
    assert status["yandex"]["customer_traffic_enabled"] is False
    assert status["yandex"]["billing_policy"] == "blocked_until_unified_billing"
