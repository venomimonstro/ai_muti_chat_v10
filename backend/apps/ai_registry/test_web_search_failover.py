from unittest.mock import patch

from django.test import SimpleTestCase

from apps.ai_registry.web_tools import SearchResult, WebToolError, search_web


RESULT = [SearchResult(title="Result", url="https://example.com/a", snippet="fresh")]


class WebSearchFailoverTests(SimpleTestCase):
    @patch.dict(
        "os.environ",
        {
            "WEB_SEARCH_BASE_URL": "http://searxng:8080",
            "WEB_SEARCH_PROVIDER_ORDER": "searx,yandex",
            "WEB_SEARCH_PAID_PROVIDERS_ENABLED": "false",
            "WEB_SEARCH_PAID_BILLING_READY": "false",
        },
        clear=False,
    )
    @patch("apps.ai_registry.web_tools._yandex_search_config")
    @patch("apps.ai_registry.web_tools._search_yandex")
    @patch("apps.ai_registry.web_tools._search_searx")
    def test_free_searx_is_preferred_and_paid_search_stays_disabled(self, searx, yandex, config):
        config.return_value = {"api_key": "secret", "folder_id": "folder"}
        searx.return_value = RESULT
        assert search_web("query") == RESULT
        searx.assert_called_once()
        yandex.assert_not_called()

    @patch.dict(
        "os.environ",
        {
            "WEB_SEARCH_BASE_URL": "http://searxng:8080",
            "WEB_SEARCH_PROVIDER_ORDER": "searx,yandex",
            "WEB_SEARCH_PAID_PROVIDERS_ENABLED": "false",
            "WEB_SEARCH_PAID_BILLING_READY": "false",
        },
        clear=False,
    )
    @patch("apps.ai_registry.web_tools._yandex_search_config")
    @patch("apps.ai_registry.web_tools._search_yandex")
    @patch("apps.ai_registry.web_tools._search_searx")
    def test_paid_yandex_is_not_silently_used_when_free_search_fails(self, searx, yandex, config):
        config.return_value = {"api_key": "secret", "folder_id": "folder"}
        searx.side_effect = WebToolError("searx down")
        with self.assertRaisesRegex(WebToolError, "All web search providers failed"):
            search_web("query")
        yandex.assert_not_called()

    @patch.dict(
        "os.environ",
        {
            "WEB_SEARCH_BASE_URL": "http://searxng:8080",
            "WEB_SEARCH_PROVIDER_ORDER": "searx,yandex",
            "WEB_SEARCH_PAID_PROVIDERS_ENABLED": "true",
            "WEB_SEARCH_PAID_BILLING_READY": "true",
        },
        clear=False,
    )
    @patch("apps.ai_registry.web_tools._yandex_search_config")
    @patch("apps.ai_registry.web_tools._search_yandex")
    @patch("apps.ai_registry.web_tools._search_searx")
    def test_yandex_can_be_explicit_internal_fallback(self, searx, yandex, config):
        config.return_value = {"api_key": "secret", "folder_id": "folder"}
        searx.side_effect = WebToolError("searx down")
        yandex.return_value = RESULT
        assert search_web("query") == RESULT
        searx.assert_called_once()
        yandex.assert_called_once()

    @patch.dict(
        "os.environ",
        {
            "WEB_SEARCH_BASE_URL": "http://searxng:8080",
            "WEB_SEARCH_PROVIDER_ORDER": "yandex,searx",
            "WEB_SEARCH_PAID_PROVIDERS_ENABLED": "true",
            "WEB_SEARCH_PAID_BILLING_READY": "true",
        },
        clear=False,
    )
    @patch("apps.ai_registry.web_tools._yandex_search_config")
    @patch("apps.ai_registry.web_tools._search_yandex")
    @patch("apps.ai_registry.web_tools._search_searx")
    def test_free_searx_is_fallback_when_explicit_paid_first_fails(self, searx, yandex, config):
        config.return_value = {"api_key": "secret", "folder_id": "folder"}
        yandex.side_effect = WebToolError("yandex down")
        searx.return_value = RESULT
        assert search_web("query") == RESULT
        yandex.assert_called_once()
        searx.assert_called_once()

    @patch.dict(
        "os.environ",
        {
            "WEB_SEARCH_BASE_URL": "http://searxng:8080",
            "WEB_SEARCH_PROVIDER_ORDER": "searx,yandex",
            "WEB_SEARCH_PAID_PROVIDERS_ENABLED": "true",
            "WEB_SEARCH_PAID_BILLING_READY": "true",
        },
        clear=False,
    )
    @patch("apps.ai_registry.web_tools._yandex_search_config")
    @patch("apps.ai_registry.web_tools._search_yandex")
    @patch("apps.ai_registry.web_tools._search_searx")
    def test_failure_is_reported_only_after_all_enabled_providers_fail(self, searx, yandex, config):
        config.return_value = {"api_key": "secret", "folder_id": "folder"}
        searx.side_effect = WebToolError("searx down")
        yandex.side_effect = WebToolError("yandex down")
        with self.assertRaisesRegex(WebToolError, "All web search providers failed"):
            search_web("query")
