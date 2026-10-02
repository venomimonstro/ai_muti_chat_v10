"""Read-only isolated Chromium. All page networking uses the pinned transport."""
import os
from importlib.util import find_spec
from pathlib import Path

from .public_network import PublicNetworkError, public_page


def browser_available():
    if find_spec("playwright") is None:
        return False
    configured = os.getenv("AGENT_BROWSER_EXECUTABLE", "")
    if configured:
        return Path(configured).is_file()
    root = Path(os.getenv("PLAYWRIGHT_BROWSERS_PATH", str(Path.home() / ".cache/ms-playwright")))
    return any(root.glob("chromium*/**/chrome")) or any(root.glob("chromium*/**/headless_shell")) or any(root.glob("chromium*/**/chrome-headless-shell"))


def read_browser_page(url, query=""):
    # Fetch before launching. Redirects are validated individually and DNS is pinned.
    final_url, response = public_page(url)
    if not browser_available():
        raise PublicNetworkError("Chromium не установлен. Администратору нужно установить браузер Agent Studio")
    from playwright.sync_api import Error, sync_playwright

    try:
        with sync_playwright() as driver:
            options = {"headless": True, "timeout": 15000, "args": ["--disable-gpu", "--disable-software-rasterizer"]}
            if os.getenv("AGENT_BROWSER_EXECUTABLE"):
                options["executable_path"] = os.environ["AGENT_BROWSER_EXECUTABLE"]
            browser = driver.chromium.launch(**options)
            try:
                context = browser.new_context(java_script_enabled=False, service_workers="block", accept_downloads=False)
                # Main document is supplied by the safe transport. No subresources,
                # websockets, cookies, account session or direct browser egress.
                def route_request(route):
                    if route.request.is_navigation_request() and route.request.url == final_url:
                        route.fulfill(status=200, content_type=response["content_type"], body=response["body"])
                    else:
                        route.abort()
                context.route("**/*", route_request)
                page = context.new_page()
                page.goto(final_url, wait_until="domcontentloaded", timeout=20000)
                title = page.title()[:300]
                text = page.locator("body").inner_text(timeout=5000)[:18000]
                context.close()
            finally:
                browser.close()
    except Error as exc:
        raise PublicNetworkError("Не удалось открыть страницу в браузере") from exc
    if query.strip():
        lines = [line for line in text.splitlines() if query.casefold() in line.casefold()]
        matches = "\n".join(lines)[:8000]
    else:
        matches = text
    return {"url": final_url, "title": title, "text": text, "matches": matches, "query": query, "read_only": True}
