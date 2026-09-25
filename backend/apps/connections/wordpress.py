from urllib.parse import urljoin

import httpx
from django.core.exceptions import ValidationError

from apps.ai_registry.web_tools import WebToolError, _assert_public_http_url


TIMEOUT = 15.0


def normalize_wordpress_url(value):
    base = str(value or "").strip().rstrip("/") + "/"
    try:
        _assert_public_http_url(base)
    except WebToolError as exc:
        raise ValidationError("WordPress URL должен указывать на публичный безопасный HTTP(S) адрес") from exc
    return base.rstrip("/")


def _auth(connection):
    secret = connection.get_secret()
    if not connection.username or not secret:
        raise ValidationError("Укажите пользователя WordPress и Application Password")
    return httpx.BasicAuth(connection.username, secret)


def _endpoint(connection, path):
    base = normalize_wordpress_url(connection.base_url) + "/"
    url = urljoin(base, path.lstrip("/"))
    try:
        _assert_public_http_url(url)
    except WebToolError as exc:
        raise ValidationError("Небезопасный WordPress endpoint") from exc
    return url


def check_wordpress(connection):
    try:
        response = httpx.get(
            _endpoint(connection, "/wp-json/wp/v2/users/me"),
            params={"context": "edit"},
            auth=_auth(connection),
            headers={"User-Agent": "AIWorkspace-WordPress/1.0"},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except ValidationError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError("Не удалось авторизоваться в WordPress. Проверьте URL, пользователя и Application Password") from exc
    user_id = payload.get("id")
    if not user_id:
        raise ValidationError("WordPress не подтвердил текущего пользователя")
    return {
        "user_id": int(user_id),
        "name": str(payload.get("name") or payload.get("slug") or connection.username)[:160],
    }


def create_wordpress_post(connection, *, title, content, status="draft", slug=""):
    if status not in {"draft", "publish"}:
        raise ValidationError("WordPress поддерживает только draft или publish")
    title = str(title or "").strip()
    content = str(content or "").strip()
    if not title or not content:
        raise ValidationError("Для публикации нужны заголовок и текст")
    body = {"title": title[:500], "content": content, "status": status}
    if slug:
        body["slug"] = str(slug).strip()[:200]
    try:
        response = httpx.post(
            _endpoint(connection, "/wp-json/wp/v2/posts"),
            json=body,
            auth=_auth(connection),
            headers={"User-Agent": "AIWorkspace-WordPress/1.0"},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except ValidationError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError("WordPress не принял публикацию") from exc
    post_id = payload.get("id")
    if not post_id:
        raise ValidationError("WordPress вернул некорректный ответ при создании записи")
    return {
        "post_id": int(post_id),
        "status": str(payload.get("status") or status),
        "url": str(payload.get("link") or ""),
        "slug": str(payload.get("slug") or ""),
    }
