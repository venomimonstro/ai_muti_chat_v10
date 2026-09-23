import json
import time
import uuid
from collections.abc import Iterator

import httpx
from django.conf import settings

from .adapters import AdapterHealth, ProviderError, ProviderResult, ProviderStreamEvent, _collect, _text_only


OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_API_BASE_URL = "https://api.giga.chat/v1"
DEFAULT_SCOPE = "GIGACHAT_API_PERS"
VALID_SCOPES = {"GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"}


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


class GigaChatAPIAdapter:
    """API-only GigaChat adapter. No local inference or llama.cpp fallback."""

    def __init__(self, *, authorization_key: str, base_url: str = DEFAULT_API_BASE_URL, scope: str = DEFAULT_SCOPE):
        if not authorization_key:
            raise ProviderError("Provider credential is not configured", code="credential_missing", retryable=False)
        self.authorization_key = authorization_key.removeprefix("Basic ").strip()
        self.base_url = (base_url or DEFAULT_API_BASE_URL).rstrip("/")
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
            status = exc.response.status_code
            raise ProviderError(
                "GigaChat OAuth rejected authorization key or scope",
                code=f"gigachat_oauth_http_{status}",
                retryable=status >= 500 or status == 429,
            ) from exc
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

    def _headers(self, *, force_token: bool = False) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token(force=force_token)}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _messages(messages: list[dict]) -> list[dict]:
        return [
            {"role": item["role"], "content": _text_only(item.get("content", ""))}
            for item in messages
            if item.get("role") in {"system", "user", "assistant"}
        ]

    def _request_stream(self, *, model: str, messages: list[dict], max_output_tokens: int, force_token: bool = False) -> Iterator[ProviderStreamEvent]:
        payload = {
            "model": model,
            "messages": self._messages(messages),
            "max_tokens": max_output_tokens,
            "stream": True,
        }
        request_id = ""
        usage = {}
        with httpx.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(force_token=force_token),
            json=payload,
            timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as response:
            if response.status_code == 401 and not force_token:
                response.close()
                yield from self._request_stream(model=model, messages=messages, max_output_tokens=max_output_tokens, force_token=True)
                return
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                raise ProviderError(
                    "GigaChat request rejected",
                    code=f"gigachat_http_{status}",
                    retryable=status == 429 or status >= 500,
                ) from exc
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderError("Invalid GigaChat stream", code="gigachat_invalid_stream", retryable=True) from exc
                request_id = str(event.get("id") or request_id)
                usage = event.get("usage") or usage
                choices = event.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content") or ""
                    if text:
                        yield ProviderStreamEvent(kind="delta", text_delta=text)
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id=request_id,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )

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
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return AdapterHealth(False, int((time.monotonic() - started) * 1000), f"gigachat_http_{status or 'network'}")

    def capabilities(self):
        return {"text", "streaming"}
