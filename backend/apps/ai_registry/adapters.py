import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol

import httpx
from django.conf import settings

from .models import AIModel, Provider


@dataclass(frozen=True)
class ProviderResult:
    text: str
    input_tokens: int
    output_tokens: int
    provider_request_id: str


class ProviderError(Exception):
    def __init__(self, message: str, *, code: str = "provider_error", retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class AdapterHealth:
    healthy: bool
    latency_ms: int
    error_code: str = ""


@dataclass(frozen=True)
class ProviderStreamEvent:
    kind: Literal["delta", "completed"]
    text_delta: str = ""
    provider_request_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class ProviderAdapter(Protocol):
    def generate(
        self, *, model: str, messages: list[dict], max_output_tokens: int
    ) -> ProviderResult: ...

    def stream(
        self, *, model: str, messages: list[dict], max_output_tokens: int
    ) -> Iterator[ProviderStreamEvent]: ...

    def health_check(self) -> AdapterHealth: ...

    def capabilities(self) -> set[str]: ...


class EchoProviderAdapter:
    """Безопасный deterministic adapter для разработки и contract tests."""

    def generate(
        self, *, model: str, messages: list[dict], max_output_tokens: int
    ) -> ProviderResult:
        prompt = messages[-1]["content"]
        text = f"Тестовый ответ: {prompt}"[: max_output_tokens * 4]
        return ProviderResult(
            text=text,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            provider_request_id=f"echo:{model}",
        )

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        result = self.generate(model=model, messages=messages, max_output_tokens=max_output_tokens)
        for word in result.text.split(" "):
            yield ProviderStreamEvent(kind="delta", text_delta=f"{word} ")
        yield ProviderStreamEvent(
            kind="completed",
            provider_request_id=result.provider_request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    def health_check(self):
        return AdapterHealth(healthy=True, latency_ms=0)

    def capabilities(self):
        return {"text", "streaming"}


class HTTPAdapter:
    def _health_get(self, *, url: str, headers: dict) -> AdapterHealth:
        started = time.monotonic()
        try:
            response = httpx.get(url, headers=headers, timeout=min(settings.AI_PROVIDER_TIMEOUT_SECONDS, 10))
            response.raise_for_status()
            return AdapterHealth(True, int((time.monotonic() - started) * 1000))
        except httpx.HTTPError as exc:
            return AdapterHealth(
                False,
                int((time.monotonic() - started) * 1000),
                _http_error(exc).code,
            )


def _http_error(exc: httpx.HTTPError) -> ProviderError:
    response = getattr(exc, "response", None)
    status = response.status_code if response is not None else None
    if status == 429:
        return ProviderError("Provider rate limit", code="rate_limited", retryable=True)
    if status is not None and 400 <= status < 500:
        return ProviderError("Provider rejected request", code=f"http_{status}", retryable=False)
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("Provider timeout", code="timeout", retryable=True)
    return ProviderError("Provider request failed", code=f"http_{status or 'network'}", retryable=True)


class OpenAIResponsesAdapter(HTTPAdapter):
    """Server-side adapter for typed SSE events from the Responses API."""

    def __init__(self, *, api_key: str, base_url: str = "https://api.openai.com/v1"):
        if not api_key:
            raise ProviderError("Provider credential is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        normalized = [
            {
                "role": "developer" if item["role"] == "system" else item["role"],
                "content": item["content"],
            }
            for item in messages
        ]
        payload = {
            "model": model,
            "input": normalized,
            "max_output_tokens": max_output_tokens,
            "stream": True,
            "store": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/responses",
                headers=headers,
                json=payload,
                timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        yield ProviderStreamEvent(kind="delta", text_delta=event.get("delta", ""))
                    elif event_type == "response.completed":
                        envelope = event.get("response", {})
                        usage = envelope.get("usage") or {}
                        yield ProviderStreamEvent(
                            kind="completed",
                            provider_request_id=envelope.get("id", ""),
                            input_tokens=usage.get("input_tokens", 0),
                            output_tokens=usage.get("output_tokens", 0),
                        )
                    elif event_type in {"error", "response.failed"}:
                        raise ProviderError(event.get("message") or "Provider stream failed")
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Invalid provider stream", code="invalid_stream") from exc

    def generate(self, *, model: str, messages: list[dict], max_output_tokens: int):
        text = ""
        completed = None
        for event in self.stream(
            model=model, messages=messages, max_output_tokens=max_output_tokens
        ):
            if event.kind == "delta":
                text += event.text_delta
            else:
                completed = event
        if completed is None:
            raise ProviderError("Provider stream ended without completion event")
        return ProviderResult(
            text=text,
            input_tokens=completed.input_tokens,
            output_tokens=completed.output_tokens,
            provider_request_id=completed.provider_request_id,
        )

    def health_check(self):
        return self._health_get(
            url=f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}
        )

    def capabilities(self):
        return {"text", "streaming", "vision", "tools"}


class AnthropicMessagesAdapter(HTTPAdapter):
    def __init__(self, *, api_key: str, base_url: str = "https://api.anthropic.com/v1"):
        if not api_key:
            raise ProviderError(
                "Provider credential is not configured", code="credential_missing", retryable=False
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        system = "\n\n".join(item["content"] for item in messages if item["role"] == "system")
        payload = {
            "model": model,
            "messages": [item for item in messages if item["role"] in {"user", "assistant"}],
            "max_tokens": max_output_tokens,
            "stream": True,
        }
        if system:
            payload["system"] = system
        request_id = ""
        input_tokens = 0
        output_tokens = 0
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload,
                timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    event_type = event.get("type")
                    if event_type == "message_start":
                        envelope = event.get("message") or {}
                        request_id = envelope.get("id", "")
                        input_tokens = (envelope.get("usage") or {}).get("input_tokens", 0)
                    elif event_type == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield ProviderStreamEvent(kind="delta", text_delta=delta.get("text", ""))
                    elif event_type == "message_delta":
                        output_tokens = (event.get("usage") or {}).get(
                            "output_tokens", output_tokens
                        )
                    elif event_type == "error":
                        error = event.get("error") or {}
                        raise ProviderError(
                            "Provider stream failed",
                            code=error.get("type", "provider_error"),
                            retryable=error.get("type") != "invalid_request_error",
                        )
                    elif event_type == "message_stop":
                        yield ProviderStreamEvent(
                            kind="completed",
                            provider_request_id=request_id,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Invalid provider stream", code="invalid_stream") from exc

    def generate(self, *, model: str, messages: list[dict], max_output_tokens: int):
        return _collect(self, model=model, messages=messages, max_output_tokens=max_output_tokens)

    def health_check(self):
        return self._health_get(url=f"{self.base_url}/models", headers=self.headers)

    def capabilities(self):
        return {"text", "streaming", "vision", "tools"}


class DeepSeekChatAdapter(HTTPAdapter):
    def __init__(self, *, api_key: str, base_url: str = "https://api.deepseek.com"):
        if not api_key:
            raise ProviderError(
                "Provider credential is not configured", code="credential_missing", retryable=False
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        request_id = ""
        usage = {}
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    request_id = event.get("id", request_id)
                    usage = event.get("usage") or usage
                    choices = event.get("choices") or []
                    if choices:
                        text = (choices[0].get("delta") or {}).get("content") or ""
                        if text:
                            yield ProviderStreamEvent(kind="delta", text_delta=text)
                yield ProviderStreamEvent(
                    kind="completed",
                    provider_request_id=request_id,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                )
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Invalid provider stream", code="invalid_stream") from exc

    def generate(self, *, model: str, messages: list[dict], max_output_tokens: int):
        return _collect(self, model=model, messages=messages, max_output_tokens=max_output_tokens)

    def health_check(self):
        return self._health_get(url=f"{self.base_url}/models", headers=self.headers)

    def capabilities(self):
        return {"text", "streaming"}


class XAIChatAdapter(DeepSeekChatAdapter):
    """xAI's OpenAI-compatible Chat Completions contract."""

    def __init__(self, *, api_key: str, base_url: str = "https://api.x.ai/v1"):
        super().__init__(api_key=api_key, base_url=base_url)

    def capabilities(self):
        return {"text", "streaming", "vision", "tools"}


class GeminiGenerateContentAdapter(HTTPAdapter):
    """Google Gemini streamGenerateContent SSE adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
    ):
        if not api_key:
            raise ProviderError(
                "Provider credential is not configured",
                code="credential_missing",
                retryable=False,
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def headers(self):
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        system = "\n\n".join(
            item["content"] for item in messages if item["role"] == "system"
        )
        contents = [
            {
                "role": "model" if item["role"] == "assistant" else "user",
                "parts": [{"text": item["content"]}],
            }
            for item in messages
            if item["role"] in {"user", "assistant"}
        ]
        payload = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_output_tokens},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        request_id = ""
        usage = {}
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse",
                headers=self.headers,
                json=payload,
                timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    event = json.loads(data)
                    request_id = event.get("responseId", request_id)
                    usage = event.get("usageMetadata") or usage
                    blocked = (event.get("promptFeedback") or {}).get("blockReason")
                    if blocked:
                        raise ProviderError(
                            "Provider blocked prompt",
                            code=f"blocked_{blocked.lower()}",
                            retryable=False,
                        )
                    candidates = event.get("candidates") or []
                    if not candidates:
                        continue
                    parts = ((candidates[0].get("content") or {}).get("parts") or [])
                    for part in parts:
                        if not part.get("thought") and part.get("text"):
                            yield ProviderStreamEvent(kind="delta", text_delta=part["text"])
                yield ProviderStreamEvent(
                    kind="completed",
                    provider_request_id=request_id,
                    input_tokens=usage.get("promptTokenCount", 0),
                    output_tokens=usage.get("candidatesTokenCount", 0),
                )
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Invalid provider stream", code="invalid_stream") from exc

    def generate(self, *, model: str, messages: list[dict], max_output_tokens: int):
        return _collect(self, model=model, messages=messages, max_output_tokens=max_output_tokens)

    def health_check(self):
        return self._health_get(url=f"{self.base_url}/models", headers=self.headers)

    def capabilities(self):
        return {"text", "streaming", "vision", "tools"}


def _collect(adapter, *, model: str, messages: list[dict], max_output_tokens: int):
    text = ""
    completed = None
    for event in adapter.stream(
        model=model, messages=messages, max_output_tokens=max_output_tokens
    ):
        if event.kind == "delta":
            text += event.text_delta
        else:
            completed = event
    if completed is None:
        raise ProviderError("Provider stream ended without completion event", code="invalid_stream")
    return ProviderResult(
        text=text,
        input_tokens=completed.input_tokens,
        output_tokens=completed.output_tokens,
        provider_request_id=completed.provider_request_id,
    )


def adapter_for(model: AIModel):
    provider = model.provider
    if provider.adapter_type == Provider.AdapterType.ECHO:
        return EchoProviderAdapter()
    if provider.adapter_type == Provider.AdapterType.OPENAI_RESPONSES:
        return OpenAIResponsesAdapter(
            api_key=os.getenv(provider.credential_env or "OPENAI_API_KEY", ""),
            base_url=provider.api_base_url
            or os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
        )
    if provider.adapter_type == Provider.AdapterType.ANTHROPIC_MESSAGES:
        return AnthropicMessagesAdapter(
            api_key=os.getenv(provider.credential_env or "ANTHROPIC_API_KEY", ""),
            base_url=provider.api_base_url
            or os.getenv("ANTHROPIC_API_BASE_URL", "https://api.anthropic.com/v1"),
        )
    if provider.adapter_type == Provider.AdapterType.DEEPSEEK_CHAT:
        return DeepSeekChatAdapter(
            api_key=os.getenv(provider.credential_env or "DEEPSEEK_API_KEY", ""),
            base_url=provider.api_base_url
            or os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com"),
        )
    if provider.adapter_type == Provider.AdapterType.GEMINI_GENERATE_CONTENT:
        return GeminiGenerateContentAdapter(
            api_key=os.getenv(provider.credential_env or "GEMINI_API_KEY", ""),
            base_url=provider.api_base_url
            or os.getenv(
                "GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
            ),
        )
    if provider.adapter_type == Provider.AdapterType.XAI_CHAT:
        return XAIChatAdapter(
            api_key=os.getenv(provider.credential_env or "XAI_API_KEY", ""),
            base_url=provider.api_base_url
            or os.getenv("XAI_API_BASE_URL", "https://api.x.ai/v1"),
        )
    raise ProviderError(
        f"Unsupported adapter: {provider.adapter_type}", code="unsupported_adapter", retryable=False
    )
