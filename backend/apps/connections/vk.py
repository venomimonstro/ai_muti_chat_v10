from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from django.core.exceptions import ValidationError

VK_AUTHORIZE_URL = os.getenv("VK_OAUTH_AUTHORIZE_URL", "https://oauth.vk.com/authorize")
VK_TOKEN_URL = os.getenv("VK_OAUTH_TOKEN_URL", "https://oauth.vk.com/access_token")
VK_API_BASE_URL = os.getenv("VK_API_BASE_URL", "https://api.vk.com/method")
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
VK_SCOPES = os.getenv("VK_OAUTH_SCOPES", "wall,photos,groups,offline")
VK_TIMEOUT_SECONDS = max(3, int(os.getenv("VK_API_TIMEOUT_SECONDS", "15")))
VK_WALL_IDEMPOTENCY_PARAM = os.getenv("VK_WALL_IDEMPOTENCY_PARAM", "guid").strip()


@dataclass(frozen=True)
class VKProfile:
    user_id: str
    display_name: str
    groups: list[dict]


def _settings():
    app_id = os.getenv("VK_APP_ID", "").strip()
    app_secret = os.getenv("VK_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise ValidationError("VK OAuth не настроен: задайте VK_APP_ID и VK_APP_SECRET")
    return app_id, app_secret


def oauth_authorize_url(*, state: str, redirect_uri: str) -> str:
    app_id, _secret = _settings()
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "display": "page",
        "scope": VK_SCOPES,
        "response_type": "code",
        "v": VK_API_VERSION,
        "state": state,
    }
    return f"{VK_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(*, code: str, redirect_uri: str) -> dict:
    app_id, app_secret = _settings()
    params = {
        "client_id": app_id,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    try:
        response = httpx.get(VK_TOKEN_URL, params=params, timeout=VK_TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError("Не удалось завершить авторизацию VK") from exc
    if not isinstance(payload, dict) or payload.get("error"):
        description = str(payload.get("error_description") or payload.get("error") or "OAuth error")[:200]
        raise ValidationError(f"VK OAuth: {description}")
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise ValidationError("VK OAuth не вернул access token")
    return {
        "access_token": token,
        "user_id": str(payload.get("user_id") or ""),
        "expires_in": payload.get("expires_in"),
    }


def api_call(token: str, method: str, params: dict | None = None) -> dict:
    token = str(token or "").strip()
    if not token:
        raise ValidationError("VK access token отсутствует")
    body = {**(params or {}), "access_token": token, "v": VK_API_VERSION}
    try:
        response = httpx.post(
            f"{VK_API_BASE_URL.rstrip('/')}/{method}",
            data=body,
            timeout=VK_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError("VK API временно недоступен") from exc
    if not isinstance(payload, dict):
        raise ValidationError("VK API вернул некорректный ответ")
    if payload.get("error"):
        error = payload.get("error") or {}
        code = error.get("error_code")
        message = str(error.get("error_msg") or "VK API error")[:200]
        raise ValidationError(f"VK API {code}: {message}")
    return payload


def check_vk(connection) -> VKProfile:
    token = connection.get_secret()
    users = api_call(token, "users.get").get("response") or []
    if not users:
        raise ValidationError("VK не подтвердил пользователя")
    user = users[0] if isinstance(users, list) else {}
    user_id = str(user.get("id") or connection.metadata.get("user_id") or "")
    display_name = " ".join(
        part for part in (str(user.get("first_name") or "").strip(), str(user.get("last_name") or "").strip()) if part
    ).strip() or "VK"
    selected_group_id = str((connection.metadata or {}).get("selected_group_id") or "").strip().lstrip("-")
    groups: list[dict] = []
    try:
        result = api_call(
            token,
            "groups.get",
            {"extended": 1, "filter": "admin,editor,moder", "count": 100},
        ).get("response") or {}
        items = result.get("items") if isinstance(result, dict) else []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            groups.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or "")[:160],
                    "screen_name": str(item.get("screen_name") or "")[:160],
                    "photo_100": str(item.get("photo_100") or "")[:500],
                }
            )
    except ValidationError:
        if selected_group_id:
            raise ValidationError(
                "VK подтвердил аккаунт, но не удалось проверить права на выбранное сообщество. "
                "Автопубликация остановлена до восстановления доступа"
            )
        groups = []
    if selected_group_id and not any(str(item.get("id") or "") == selected_group_id for item in groups):
        raise ValidationError(
            "Выбранное сообщество больше недоступно с правами управления. "
            "Выберите другое сообщество или восстановите права в VK"
        )
    return VKProfile(user_id=user_id, display_name=display_name, groups=groups)


def publish_wall_post(
    connection,
    *,
    group_id: str,
    message: str,
    attachments: str = "",
    publish_date: int | None = None,
    request_guid: str = "",
) -> str:
    group_id = str(group_id or "").strip().lstrip("-")
    message = str(message or "").strip()
    if not group_id:
        raise ValidationError("Выберите сообщество VK")
    if not message and not attachments:
        raise ValidationError("Публикация VK не может быть пустой")
    params = {
        "owner_id": f"-{group_id}",
        "from_group": 1,
        "message": message,
    }
    if attachments:
        params["attachments"] = attachments
    if publish_date:
        params["publish_date"] = int(publish_date)
    if request_guid and VK_WALL_IDEMPOTENCY_PARAM:
        params[VK_WALL_IDEMPOTENCY_PARAM] = str(request_guid)[:64]
    result = api_call(connection.get_secret(), "wall.post", params).get("response") or {}
    post_id = result.get("post_id") if isinstance(result, dict) else None
    if not post_id:
        raise ValidationError("VK не вернул идентификатор публикации")
    return f"-{group_id}_{post_id}"
