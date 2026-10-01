from types import SimpleNamespace
from unittest.mock import Mock

from . import web_fetch_security


def _module(raw):
    return SimpleNamespace(_fetch_page_excerpt=raw)


def test_direct_result_page_fetch_is_disabled_without_safe_egress(monkeypatch):
    raw = Mock(return_value="secret internal page")
    module = _module(raw)
    monkeypatch.setenv("WEB_FETCH_PAGES", "true")
    monkeypatch.delenv("WEB_PAGE_FETCH_SAFE_EGRESS", raising=False)

    web_fetch_security.install(module)

    assert module._fetch_page_excerpt("https://example.com/page") == ""
    raw.assert_not_called()


def test_direct_result_page_fetch_requires_both_explicit_flags(monkeypatch):
    raw = Mock(return_value="public page")
    module = _module(raw)
    monkeypatch.setenv("WEB_FETCH_PAGES", "true")
    monkeypatch.setenv("WEB_PAGE_FETCH_SAFE_EGRESS", "true")

    web_fetch_security.install(module)

    assert module._fetch_page_excerpt("https://example.com/page") == "public page"
    raw.assert_called_once_with("https://example.com/page")
