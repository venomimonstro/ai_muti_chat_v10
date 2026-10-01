from __future__ import annotations

import os
import re
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx


DAY_MARKERS = (
    "сегодня",
    "прямо сейчас",
    "за сегодня",
    "последние новости",
    "свежие новости",
    "today",
    "breaking",
)
WEEK_MARKERS = (
    "на этой неделе",
    "за неделю",
    "последняя версия",
    "текущая версия",
    "актуальные новости",
    "latest",
    "this week",
)
NEWS_MARKERS = (
    "новост",
    "событи",
    "что произошло",
    "breaking",
    "news",
)
TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "yclid",
    "gclid",
    "fbclid",
}


def _intent(query: str) -> dict:
    text = re.sub(r"\s+", " ", str(query or "").casefold()).strip()
    time_range = ""
    if any(marker in text for marker in DAY_MARKERS):
        time_range = "day"
    elif any(marker in text for marker in WEEK_MARKERS):
        time_range = "week"
    return {
        "time_range": time_range,
        "category": "news" if any(marker in text for marker in NEWS_MARKERS) else "general",
    }


def _canonical_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_QUERY_KEYS and not key.casefold().startswith("utm_")
    ]
    return urlunparse(
        (
            parsed.scheme.casefold(),
            (parsed.hostname or "").casefold() + (f":{parsed.port}" if parsed.port else ""),
            parsed.path.rstrip("/") or "/",
            "",
            urlencode(query, doseq=True),
            "",
        )
    )


def _diversify(items: list, *, limit: int, per_domain: int = 2) -> list:
    result = []
    seen_urls = set()
    domain_counts = defaultdict(int)
    for item in items:
        canonical = _canonical_url(getattr(item, "url", ""))
        if not canonical or canonical in seen_urls:
            continue
        host = (urlparse(canonical).hostname or "").removeprefix("www.")
        if domain_counts[host] >= max(1, per_domain):
            continue
        seen_urls.add(canonical)
        domain_counts[host] += 1
        result.append(item)
        if len(result) >= limit:
            break
    return result


def install(web_tools_module) -> None:
    current = web_tools_module._search_searx
    if getattr(current, "_ai_workspace_search_v3", False):
        return

    def search_searx(query: str, *, limit: int):
        base_url = os.getenv("WEB_SEARCH_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            raise web_tools_module.WebToolError("SearXNG is not configured")
        web_tools_module._assert_search_provider_url(base_url)
        timeout = float(os.getenv("WEB_TOOL_TIMEOUT_SECONDS", "12"))
        max_results = max(1, min(limit, int(os.getenv("WEB_SEARCH_MAX_RESULTS", "8"))))
        # Ask for extra candidates because duplicate domains are removed before
        # grounding. This is still a single free/self-hosted metasearch request.
        requested = min(max(max_results * 3, max_results), 20)
        intent = _intent(query)

        def execute(*, time_range: str = ""):
            params = {
                "q": query,
                "format": "json",
                "language": "auto",
                "safesearch": 1,
                "categories": intent["category"],
            }
            if time_range:
                params["time_range"] = time_range
            try:
                response = httpx.get(
                    f"{base_url}/search",
                    params=params,
                    headers={"User-Agent": "AIWorkspace-WebTool/3.0", "Accept": "application/json"},
                    timeout=timeout,
                    follow_redirects=False,
                )
                response.raise_for_status()
                payload = response.json()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                hint = " (check search.formats includes json)" if status == 403 else ""
                raise web_tools_module.WebToolError(f"SearXNG HTTP {status}{hint}") from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise web_tools_module.WebToolError("SearXNG provider failed") from exc

            rows = []
            for raw in (payload.get("results") or [])[:requested]:
                url = str(raw.get("url") or "").strip()
                try:
                    web_tools_module._assert_public_http_url(url)
                except web_tools_module.WebToolError:
                    continue
                rows.append(
                    web_tools_module.SearchResult(
                        title=str(raw.get("title") or url).strip()[:300],
                        url=url,
                        snippet=str(raw.get("content") or raw.get("snippet") or "").strip()[:2000],
                        published_at=str(
                            raw.get("publishedDate") or raw.get("published_date") or ""
                        ).strip()[:80],
                    )
                )
            return _diversify(rows, limit=max_results)

        # Freshness is preferred, never mandatory. Some free engines do not
        # implement SearXNG time_range consistently, so an empty fresh pass is
        # retried without the filter before the paid provider is considered.
        results = execute(time_range=intent["time_range"])
        if not results and intent["time_range"]:
            results = execute(time_range="")
        if not results:
            raise web_tools_module.WebToolError("SearXNG returned no usable results")
        return results

    search_searx._ai_workspace_search_v3 = True
    search_searx._raw_search_searx = current
    web_tools_module._search_searx = search_searx
