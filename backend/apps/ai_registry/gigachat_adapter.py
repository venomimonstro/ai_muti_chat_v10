import json
import time
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse

import httpx
from django.conf import settings

from .adapters import (
    AdapterHealth,
    ProviderError,
    ProviderResult,
    ProviderStreamEvent,
    _chat_completion_event,
    _collect,
    _text_only,
)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_API_BASE_URL = "https://api.giga.chat/v1"
DEFAULT_SCOPE = "GIGACHAT_API_PERS"
VALID_SCOPES = {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}
LEGACY_API_HOSTS = {"gigachat.devices.sberbank.ru"}
MODEL_ALIASES = {
    "gigachat": "GigaChat-2",
    "gigachat-lite": "GigaChat-2",
    "gigachat-2-lite": "GigaChat-2",
    "gigachat-pro": "GigaChat-2-Pro",
    "gigachat-max": "GigaChat-2-Max",
}


def normalize_base_url(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return DEFAULT_API_BASE_URL
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return DEFAULT_API_BASE_URL
    if host in LEGACY_API_HOSTS:
        return DEFAULT_API_BASE_URL
    if host == "api.giga.chat":
        path = parsed.path.rstrip("/")
        if not path:
            return DEFAULT_API_BASE_URL
        if path == "/v1":
            return DEFAULT_API_BASE_URL
    return raw


def normalize_model_id(value: str) -> str:
    model = str(value or "").strip()
    if not model:
        return "GigaChat-2"
    return MODEL_ALIASES.get(model.casefold(), model)


def configured_scope(fallback: str = DEFAULT_SCOPE) -> str:
    try:
        from .models import Provider
        config = Provider.objects.filter(slug="gigachat").values_list("auth_config", flat=True).first() or {}
        value = str(config.get("scope") or "").strip()
        if value in VALID_SCOPES:
            return value
    except Exception:
        pass
    value = str(fallback or DEFAULT_SCOPE).strip()
    return value if value in VALID_SCOPES else DEFAULT_SCOPE


def _request_timeout() -> httpx.Timeout:
    total = max(5.0, float(settings.AI_PROVIDER_TIMEOUT_SECONDS))
    return httpx.Timeout(total, connect=min(10.0, total), read=total, write=min(20.0, total), pool=min(10.0, total))


def _safe_error_detail(response: httpx.Response) -> str:
    """Return a short upstream error detail without headers/credentials."""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            raw = (
                payload.get("message")
                or payload.get("detail")
                or payload.get("error")
                or payload.get("status")
                or ""
            )
            if isinstance(raw, dict):
                raw = raw.get("message") or raw.get("detail") or raw.get("code") or raw
            detail = str(raw or "").strip()
        else:
            detail = ""
    except (ValueError, TypeError, httpx.ResponseNotRead):
        detail = ""
    if not detail:
        try:
            detail = str(response.text or "").strip()
        except Exception:
            detail = ""
    return " ".join(detail.split())[:500]


def _http_provider_error(response: httpx.Response, *, oauth: bool = False) -> ProviderError:
    status = response.status_code
    prefix = "gigachat_oauth" if oauth else "gigachat"
    if status == 401:
        code = f"{prefix}_authentication_error"
        retryable = False
        message = "GigaChat authentication failed"
    elif status == 402:
        code = f"{prefix}_quota_exhausted"
        retryable = False
        message = "GigaChat token quota or provider balance is exhausted"
    elif status == 403:
        code = f"{prefix}_permission_denied"
        retryable = False
        message = "GigaChat permission denied"
    elif status == 404:
        code = f"{prefix}_model_not_found"
        retryable = False
        message = "GigaChat model or endpoint was not found"
    elif status == 413:
        code = f"{prefix}_request_too_large"
        retryable = False
        message = "GigaChat request is too large"
    elif status == 422:
        code = f"{prefix}_validation_error"
        retryable = False
        message = "GigaChat rejected request content"
    elif status == 429:
        code = f"{prefix}_rate_limited"
        retryable = True
        message = "GigaChat rate limit reached"
    elif status >= 500:
        code = f"{prefix}_server_error"
        retryable = True
        message = "GigaChat service error"
    elif status == 400:
        code = f"{prefix}_bad_request"
        retryable = False
        message = "GigaChat rejected request parameters"
    else:
        code = f"{prefix}_http_{status}"
        retryable = status >= 500
        message = "GigaChat request rejected"
    detail = _safe_error_detail(response)
    if detail:
        message = f"{message}: {detail}"
    return ProviderError(message, code=code, retryable=retryable)


class GigaChatAPIAdapter:
    """API-only GigaChat adapter. No local inference or llama.cpp fallback."""

    def __init__(self, *, authorization_key: str, base_url: str = DEFAULT_API_BASE_URL, scope: str = DEFAULT_SCOPE):
        if not authorization_key:
            raise ProviderError("Provider credential is not configured", code="credential_missing", retryable=False)
        self.authorization_key = authorization_key.removeprefix("Basic ").strip()
        self.base_url = normalize_base_url(base_url)
        self.scope = configured_scope(scope)
        self._token = ""
        self._token_expires_at = 0.0

    def _access_token(self, *, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and now < self._token_expires_at - 60:
            return self._token
        headers = {
            "Authorization": f"Basic {self.authorization_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        try:
            response = httpx.post(
                OAUTH_URL,
                headers=headers,
                data={"scope": self.scope},
                timeout=min(settings.AI_PROVIDER_TIMEOUT_SECONDS, 20),
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise _http_provider_error(exc.response, oauth=True) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("GigaChat OAuth failed", code="gigachat_oauth_network", retryable=True) from exc
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise ProviderError("GigaChat OAuth returned no access token", code="gigachat_oauth_invalid", retryable=True)
        expires_at_raw = payload.get("expires_at")
        if expires_at_raw:
            expires_at = float(expires_at_raw)
            self._token_expires_at = expires_at / 1000.0 if expires_at > 10_000_000_000 else expires_at
        else:
            self._token_expires_at = now + 29 * 60
        self._token = token
        return token

    def _headers(self, *, force_token: bool = False, stream: bool = False) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token(force=force_token)}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }

    @staticmethod
    def _messages(messages: list[dict]) -> list[dict]:
        system_parts = []
        dialogue = []
        for item in messages:
            role = str(item.get("role") or "").strip()
            if role not in {"system", "user", "assistant"}:
                continue
            content = _text_only(item.get("content", "")).strip()
            # GigaChat rejects malformed/empty history entries. The customer runtime
            # may contain an empty placeholder assistant message while a response is
            # being prepared; never forward that placeholder upstream.
            if not content:
                continue
            if role == "system":
                system_parts.append(content)
                continue
            dialogue.append({"role": role, "content": content})

        # Customer context assembly can legitimately create several internal system
        # blocks (base prompt, project context, memory, retrieval metadata). GigaChat
        # requires the system message to be first and rejects multiple system
        # messages. Collapse all internal blocks into one provider-compatible prompt.
        normalized = []
        if system_parts:
            normalized.append(
                {
                    "role": "system",
                    "content": "\n\n".join(system_parts),
                }
            )
        normalized.extend(dialogue)
        return normalized

    def _request_stream(
        self,
        *,
        model: str,
        messages: list[dict],
        max_output_tokens: int,
        force_token: bool = False,
        compatibility_retry: bool = False,
    ) -> Iterator[ProviderStreamEvent]:
        upstream_model = normalize_model_id(model)
        normalized_messages = self._messages(messages)
        if not normalized_messages:
            raise ProviderError(
                "GigaChat request contains no usable messages",
                code="gigachat_validation_error",
                retryable=False,
            )
        payload = {
            "model": upstream_model,
            "messages": normalized_messages,
            "stream": True,
        }
        # Older/current GigaChat deployments differ in how strictly they validate
        # max_tokens. Use it normally, then retry a 400 once with the provider's
        # default output limit. A 400 means no inference was accepted, so this retry
        # cannot duplicate a charged generation.
        if not compatibility_retry:
            payload["max_tokens"] = max(1, int(max_output_tokens))
        request_id = ""
        usage = {}
        finished = False
        with httpx.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(force_token=force_token, stream=True),
            json=payload,
            timeout=_request_timeout(),
            follow_redirects=True,
        ) as response:
            if response.status_code == 401 and not force_token:
                response.close()
                yield from self._request_stream(
                    model=upstream_model,
                    messages=messages,
                    max_output_tokens=max_output_tokens,
                    force_token=True,
                    compatibility_retry=compatibility_retry,
                )
                return
            if response.status_code == 400 and not compatibility_retry:
                response.close()
                yield from self._request_stream(
                    model=upstream_model,
                    messages=messages,
                    max_output_tokens=max_output_tokens,
                    force_token=force_token,
                    compatibility_retry=True,
                )
                return
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # httpx.stream() does not preload the response body. Error
                # normalization reads JSON/text, so explicitly consume the small
                # error body first; otherwise httpx raises ResponseNotRead and the
                # customer sees a generic internal-stream failure instead of the
                # real provider error.
                try:
                    exc.response.read()
                except httpx.HTTPError:
                    pass
                raise _http_provider_error(exc.response) from exc
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    finished = True
                    break
                if not data:
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderError("Invalid GigaChat stream", code="gigachat_invalid_stream", retryable=True) from exc
                request_id = str(event.get("id") or request_id)
                usage = event.get("usage") or usage
                choices = event.get("choices") or []
                finished = finished or any(choice.get("finish_reason") is not None for choice in choices)
                if choices:
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content") or ""
                    if text:
                        yield ProviderStreamEvent(kind="delta", text_delta=text)
        yield _chat_completion_event(request_id, usage, finished)

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        try:
            yield from self._request_stream(model=model, messages=messages, max_output_tokens=max_output_tokens)
        except httpx.TimeoutException as exc:
            raise ProviderError("GigaChat timeout", code="timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("GigaChat network error", code="gigachat_network", retryable=True) from exc

    def generate(self, *, model: str, messages: list[dict], max_output_tokens: int) -> ProviderResult:
        return _collect(self, model=model, messages=messages, max_output_tokens=max_output_tokens)

    def health_check(self) -> AdapterHealth:
        started = time.monotonic()
        try:
            response = httpx.get(
                f"{self.base_url}/models",
                headers=self._headers(),
                timeout=min(settings.AI_PROVIDER_TIMEOUT_SECONDS, 10),
                follow_redirects=True,
            )
            response.raise_for_status()
            return AdapterHealth(True, int((time.monotonic() - started) * 1000))
        except ProviderError as exc:
            return AdapterHealth(False, int((time.monotonic() - started) * 1000), exc.code)
        except httpx.HTTPStatusError as exc:
            error = _http_provider_error(exc.response)
            return AdapterHealth(False, int((time.monotonic() - started) * 1000), error.code)
        except httpx.HTTPError:
            return AdapterHealth(False, int((time.monotonic() - started) * 1000), "gigachat_network")

    def capabilities(self):
        return {"text", "streaming"}
