from __future__ import annotations

import os


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().casefold() in {"1", "true", "yes", "on"}


def _searx_urls() -> list[str]:
    raw = os.getenv("WEB_SEARCH_BASE_URLS", "").strip()
    values = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    legacy = os.getenv("WEB_SEARCH_BASE_URL", "").strip().rstrip("/")
    if legacy and legacy not in values:
        values.append(legacy)
    return values


def install(web_tools_module) -> None:
    """Prefer redundant free SearXNG and gate paid search explicitly.

    Paid search must not become an invisible platform cost merely because an API
    credential exists. Until paid-search cost accounting is enabled deliberately,
    customer traffic remains on free/self-hosted search only.
    """
    if getattr(web_tools_module.search_web, "_ai_workspace_search_runtime", False):
        return

    raw_search_searx = web_tools_module._search_searx
    raw_provider_order = web_tools_module._search_provider_order

    def searx_search_status():
        urls = _searx_urls()
        return {
            "configured": bool(urls),
            "endpoint": urls[0] if urls else "",
            "endpoints": urls,
            "endpoint_count": len(urls),
        }

    def search_searx(query: str, *, limit: int):
        urls = _searx_urls()
        if not urls:
            raise web_tools_module.WebToolError("SearXNG is not configured")
        original = os.environ.get("WEB_SEARCH_BASE_URL")
        errors = []
        try:
            for url in urls:
                os.environ["WEB_SEARCH_BASE_URL"] = url
                try:
                    return raw_search_searx(query, limit=limit)
                except web_tools_module.WebToolError as exc:
                    errors.append(f"{url}: {exc}")
                    web_tools_module.logger.warning(
                        "SearXNG endpoint failed; trying next free endpoint: %s", exc
                    )
        finally:
            if original is None:
                os.environ.pop("WEB_SEARCH_BASE_URL", None)
            else:
                os.environ["WEB_SEARCH_BASE_URL"] = original
        raise web_tools_module.WebToolError(
            "All SearXNG endpoints failed: " + "; ".join(errors)
        )

    def provider_order():
        order = raw_provider_order()
        paid_enabled = _truthy("WEB_SEARCH_PAID_PROVIDERS_ENABLED", "false")
        if not paid_enabled:
            order = [item for item in order if item != "yandex"]
        return order or ["searx"]

    def web_search_status():
        return {
            "provider_order": provider_order(),
            "searx": searx_search_status(),
            "yandex": {
                **web_tools_module.yandex_search_status(),
                "customer_traffic_enabled": _truthy(
                    "WEB_SEARCH_PAID_PROVIDERS_ENABLED", "false"
                ),
                "billing_policy": "included_in_answer_total",
            },
        }

    search_searx._ai_workspace_search_runtime = True
    web_tools_module._search_searx = search_searx
    web_tools_module._search_provider_order = provider_order
    web_tools_module.searx_search_status = searx_search_status
    web_tools_module.web_search_status = web_search_status
    # search_web resolves these globals at call time, so no wrapper is required.
    web_tools_module.search_web._ai_workspace_search_runtime = True
