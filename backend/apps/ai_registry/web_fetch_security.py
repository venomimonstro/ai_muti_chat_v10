"""Harden direct result-page fetching against DNS-rebinding SSRF.

Search-provider snippets remain available. Fetching arbitrary result URLs requires an
operator-controlled egress boundary because a pre-request DNS check alone cannot pin
the address used by a later HTTP client connection.
"""

import os


def _enabled() -> bool:
    requested = os.getenv("WEB_FETCH_PAGES", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    safe_egress = os.getenv("WEB_PAGE_FETCH_SAFE_EGRESS", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return requested and safe_egress


def install(web_tools_module) -> None:
    raw = web_tools_module._fetch_page_excerpt
    if getattr(raw, "_ai_workspace_safe_egress", False):
        return

    def fetch_page_excerpt(url: str) -> str:
        if not _enabled():
            return ""
        return raw(url)

    fetch_page_excerpt._ai_workspace_safe_egress = True
    fetch_page_excerpt._raw_fetch_page_excerpt = raw
    web_tools_module._fetch_page_excerpt = fetch_page_excerpt
