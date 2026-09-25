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


def _request(connection, method, path, *, params=None, json=None, error_message):
    try:
        response = httpx.request(
            method,
            _endpoint(connection, path),
            params=params,
            json=json,
            auth=_auth(connection),
            headers={"User-Agent": "AIWorkspace-WordPress/1.0"},
            timeout=TIMEOUT,
            follow_redirects=False,
        )
        response.raise_for_status()
        return response.json()
    except ValidationError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError(error_message) from exc


def check_wordpress(connection):
    payload = _request(
        connection,
        "GET",
        "/wp-json/wp/v2/users/me",
        params={"context": "edit"},
        error_message="Не удалось авторизоваться в WordPress. Проверьте URL, пользователя и Application Password",
    )
    user_id = payload.get("id")
    if not user_id:
        raise ValidationError("WordPress не подтвердил текущего пользователя")
    return {
        "user_id": int(user_id),
        "name": str(payload.get("name") or payload.get("slug") or connection.username)[:160],
    }


def _post_payload(payload, fallback_status):
    post_id = payload.get("id")
    if not post_id:
        raise ValidationError("WordPress вернул некорректный ответ при сохранении записи")
    return {
        "post_id": int(post_id),
        "status": str(payload.get("status") or fallback_status),
        "url": str(payload.get("link") or ""),
        "slug": str(payload.get("slug") or ""),
    }


def find_wordpress_post_by_slug(connection, slug):
    slug = str(slug or "").strip()
    if not slug:
        return None
    payload = _request(
        connection,
        "GET",
        "/wp-json/wp/v2/posts",
        params={"slug": slug[:200], "context": "edit", "per_page": 1},
        error_message="WordPress не ответил при проверке существующей записи",
    )
    if not isinstance(payload, list) or not payload:
        return None
    result = _post_payload(payload[0], str(payload[0].get("status") or "draft"))
    result["existing"] = True
    return result


def create_wordpress_post(connection, *, title, content, status="draft", slug=""):
    if status not in {"draft", "publish"}:
        raise ValidationError("WordPress поддерживает только draft или publish")
    title = str(title or "").strip()
    content = str(content or "").strip()
    slug = str(slug or "").strip()[:200]
    if not title or not content:
        raise ValidationError("Для публикации нужны заголовок и текст")

    # WordPress core has no generic Idempotency-Key support. A stable unique
    # slug therefore acts as our retry key: if a worker dies after WordPress
    # accepted the POST but before our DB commit, the retry reuses that post.
    if slug:
        existing = find_wordpress_post_by_slug(connection, slug)
        if existing is not None:
            return existing

    body = {"title": title[:500], "content": content, "status": status}
    if slug:
        body["slug"] = slug
    payload = _request(
        connection,
        "POST",
        "/wp-json/wp/v2/posts",
        json=body,
        error_message="WordPress не принял публикацию",
    )
    result = _post_payload(payload, status)
    result["existing"] = False
    return result


def update_wordpress_post(connection, *, post_id, status=None, title=None, content=None):
    try:
        post_id = int(post_id)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Некорректный WordPress post id") from exc
    if post_id <= 0:
        raise ValidationError("Некорректный WordPress post id")
    body = {}
    if status is not None:
        if status not in {"draft", "publish"}:
            raise ValidationError("WordPress поддерживает только draft или publish")
        body["status"] = status
    if title is not None:
        body["title"] = str(title).strip()[:500]
    if content is not None:
        body["content"] = str(content).strip()
    if not body:
        raise ValidationError("Нет изменений для WordPress записи")
    payload = _request(
        connection,
        "POST",
        f"/wp-json/wp/v2/posts/{post_id}",
        json=body,
        error_message="WordPress не обновил запись",
    )
    return _post_payload(payload, status or "draft")
