from unittest.mock import patch

import pytest

from apps.ai_registry.web_tools import WebToolError, _assert_public_http_url


def test_rejects_non_http_urls():
    with pytest.raises(WebToolError):
        _assert_public_http_url("file:///etc/passwd")


@patch("apps.ai_registry.web_tools.socket.getaddrinfo")
def test_rejects_private_resolved_address(getaddrinfo):
    getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 80))]
    with pytest.raises(WebToolError):
        _assert_public_http_url("http://example.invalid")


@patch("apps.ai_registry.web_tools.socket.getaddrinfo")
def test_accepts_public_resolved_address(getaddrinfo):
    getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]
    _assert_public_http_url("https://example.com")
