from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.ai_registry.web_tools import SearchResult, WebToolError, search_web


RESULT = [SearchResult(title="Result", url="https://example.com/a", snippet="fresh")]


class WebSearchFailoverTests(SimpleTestCase):
    @patch.dict(
        "os.environ",
        {
            "WEB_SEARCH_BASE_URL": "http://searxng:8080",
            "WEB_SEARCH_PROVIDER_ORDER": "searx,yandex",
        },
        clear=False,
    )
    @patch("apps.ai_registry.web_tools._yandex_search_config")
    @patch("apps.ai_registry.web_tools._search_yandex")
    @patch("apps.ai_registry.web_tools._search_searx")
    def test_free_searx_is_preferred_by_default(self, searx, yandex, config):
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
        },
        clear=False,
    )
    @patch("apps.ai_registry.web_tools._yandex_search_config")
    @patch("apps.ai_registry.web_tools._search_yandex")
    @patch("apps.ai_registry.web_tools._search_searx")
    def test_yandex_is_used_when_searx_fails(self, searx, yandex, config):
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
        },
        clear=False,
    )
    @patch("apps.ai_registry.web_tools._yandex_search_config")
    @patch("apps.ai_registry.web_tools._search_yandex")
    @patch("apps.ai_registry.web_tools._search_searx")
    def test_searx_is_fallback_when_yandex_first_fails(self, searx, yandex, config):
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
        },
        clear=False,
    )
    @patch("apps.ai_registry.web_tools._yandex_search_config")
    @patch("apps.ai_registry.web_tools._search_yandex")
    @patch("apps.ai_registry.web_tools._search_searx")
    def test_failure_is_reported_only_after_all_providers_fail(self, searx, yandex, config):
        config.return_value = {"api_key": "secret", "folder_id": "folder"}
        searx.side_effect = WebToolError("searx down")
        yandex.side_effect = WebToolError("yandex down")
        with self.assertRaisesRegex(WebToolError, "All web search providers failed"):
            search_web("query")
