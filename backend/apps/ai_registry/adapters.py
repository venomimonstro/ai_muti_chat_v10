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
    def generate(self, *, model: str, messages: list[dict], max_output_tokens: int) -> ProviderResult: ...
    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int) -> Iterator[ProviderStreamEvent]: ...
    def health_check(self) -> AdapterHealth: ...
    def capabilities(self) -> set[str]: ...


def _generic_blocks(content):
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": str(content)}]


def _text_only(content):
    return "\n".join(block.get("text", "") for block in _generic_blocks(content) if block.get("type") == "text")


def _data_url(block):
    return f"data:{block['media_type']};base64,{block['data']}"


def _openai_responses_content(content):
    result = []
    for block in _generic_blocks(content):
        if block.get("type") == "text":
            result.append({"type": "input_text", "text": block.get("text", "")})
        elif block.get("type") == "image":
            result.append({"type": "input_image", "image_url": _data_url(block)})
    return result


def _openai_chat_content(content):
    blocks = _generic_blocks(content)
    if all(block.get("type") == "text" for block in blocks):
        return "\n".join(block.get("text", "") for block in blocks)
    result = []
    for block in blocks:
        if block.get("type") == "text":
            result.append({"type": "text", "text": block.get("text", "")})
        elif block.get("type") == "image":
            result.append({"type": "image_url", "image_url": {"url": _data_url(block)}})
    return result


def _anthropic_content(content):
    result = []
    for block in _generic_blocks(content):
        if block.get("type") == "text":
            result.append({"type": "text", "text": block.get("text", "")})
        elif block.get("type") == "image":
            result.append({"type": "image", "source": {"type": "base64", "media_type": block["media_type"], "data": block["data"]}})
    return result


def _gemini_parts(content):
    result = []
    for block in _generic_blocks(content):
        if block.get("type") == "text":
            result.append({"text": block.get("text", "")})
        elif block.get("type") == "image":
            result.append({"inlineData": {"mimeType": block["media_type"], "data": block["data"]}})
    return result


class EchoProviderAdapter:
    def generate(self, *, model: str, messages: list[dict], max_output_tokens: int) -> ProviderResult:
        prompt = _text_only(messages[-1]["content"])
        text = f"Тестовый ответ: {prompt}"[: max_output_tokens * 4]
        return ProviderResult(text=text, input_tokens=max(1, len(prompt) // 4), output_tokens=max(1, len(text) // 4), provider_request_id=f"echo:{model}")

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        result = self.generate(model=model, messages=messages, max_output_tokens=max_output_tokens)
        for word in result.text.split(" "):
            yield ProviderStreamEvent(kind="delta", text_delta=f"{word} ")
        yield ProviderStreamEvent(kind="completed", provider_request_id=result.provider_request_id, input_tokens=result.input_tokens, output_tokens=result.output_tokens)

    def health_check(self):
        return AdapterHealth(healthy=True, latency_ms=0)

    def capabilities(self):
        return {"text", "streaming"}


class HTTPAdapter:
    def _health_get(self, *, url: str, headers: dict) -> AdapterHealth:
        started = time.monotonic()
        try:
            response = httpx.get(url, headers=headers, timeout=min(settings.AI_PROVIDER_TIMEOUT_SECONDS, 10), follow_redirects=True)
            response.raise_for_status()
            return AdapterHealth(True, int((time.monotonic() - started) * 1000))
        except httpx.HTTPError as exc:
            return AdapterHealth(False, int((time.monotonic() - started) * 1000), _http_error(exc).code)


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


def _openai_stream_error(event: dict) -> ProviderError:
    event_type = str(event.get("type") or "")
    response = event.get("response") if isinstance(event.get("response"), dict) else {}
    error = event.get("error") if isinstance(event.get("error"), dict) else {}
    if not error and isinstance(response.get("error"), dict):
        error = response.get("error") or {}
    code = str(error.get("code") or error.get("type") or response.get("status") or event_type or "provider_error")
    message = str(error.get("message") or event.get("message") or response.get("status_details") or "OpenAI Responses request failed")
    non_retryable = {"invalid_request_error", "invalid_request", "invalid_api_key", "authentication_error", "permission_denied", "insufficient_quota", "credit_balance_exhausted", "model_not_found", "billing_hard_limit_reached"}
    return ProviderError(message, code=code[:120], retryable=code not in non_retryable and not code.startswith("invalid_"))


def _openrouter_stream_error(event: dict) -> ProviderError:
    error = event.get("error") if isinstance(event.get("error"), dict) else {}
    metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
    code = str(error.get("code") or error.get("type") or metadata.get("error_code") or "openrouter_error")
    message = str(error.get("message") or metadata.get("raw") or event.get("message") or "OpenRouter request failed")
    non_retryable_codes = {"401", "402", "403", "404", "invalid_api_key", "insufficient_credits", "model_not_found", "permission_denied"}
    return ProviderError(message, code=f"openrouter_{code}"[:120], retryable=code not in non_retryable_codes and not code.startswith("4"))


class OpenAIResponsesAdapter(HTTPAdapter):
    def __init__(self, *, api_key: str, base_url: str = "https://api.openai.com/v1"):
        if not api_key:
            raise ProviderError("Provider credential is not configured", code="credential_missing", retryable=False)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        normalized = [{"role": "developer" if item["role"] == "system" else item["role"], "content": _openai_responses_content(item["content"])} for item in messages]
        payload = {"model": model, "input": normalized, "max_output_tokens": max_output_tokens, "stream": True, "store": False}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.stream("POST", f"{self.base_url}/responses", headers=headers, json=payload, timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS) as response:
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
                        yield ProviderStreamEvent(kind="completed", provider_request_id=envelope.get("id", ""), input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0))
                    elif event_type in {"error", "response.failed"}:
                        raise _openai_stream_error(event)
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Invalid provider stream", code="invalid_stream") from exc

    def generate(self, *, model: str, messages: list[dict], max_output_tokens: int):
        return _collect(self, model=model, messages=messages, max_output_tokens=max_output_tokens)

    def health_check(self):
        return self._health_get(url=f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"})

    def capabilities(self):
        return {"text", "streaming", "vision", "tools"}


class AnthropicMessagesAdapter(HTTPAdapter):
    def __init__(self, *, api_key: str, base_url: str = "https://api.anthropic.com/v1"):
        if not api_key:
            raise ProviderError("Provider credential is not configured", code="credential_missing", retryable=False)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def headers(self):
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        system = "\n\n".join(_text_only(item["content"]) for item in messages if item["role"] == "system")
        payload = {"model": model, "messages": [{"role": item["role"], "content": _anthropic_content(item["content"])} for item in messages if item["role"] in {"user", "assistant"}], "max_tokens": max_output_tokens, "stream": True}
        if system:
            payload["system"] = system
        request_id = ""
        input_tokens = output_tokens = 0
        try:
            with httpx.stream("POST", f"{self.base_url}/messages", headers=self.headers, json=payload, timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS) as response:
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
                        output_tokens = (event.get("usage") or {}).get("output_tokens", output_tokens)
                    elif event_type == "error":
                        error = event.get("error") or {}
                        raise ProviderError("Provider stream failed", code=error.get("type", "provider_error"), retryable=error.get("type") != "invalid_request_error")
                    elif event_type == "message_stop":
                        yield ProviderStreamEvent(kind="completed", provider_request_id=request_id, input_tokens=input_tokens, output_tokens=output_tokens)
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
            raise ProviderError("Provider credential is not configured", code="credential_missing", retryable=False)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        if any(any(block.get("type") == "image" for block in _generic_blocks(item.get("content"))) for item in messages):
            raise ProviderError("Model does not support image input", code="vision_unsupported", retryable=False)
        payload = {"model": model, "messages": [{"role": item["role"], "content": _text_only(item["content"])} for item in messages], "max_tokens": max_output_tokens, "stream": True, "stream_options": {"include_usage": True}}
        request_id = ""
        usage = {}
        try:
            with httpx.stream("POST", f"{self.base_url}/chat/completions", headers=self.headers, json=payload, timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS) as response:
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
                yield ProviderStreamEvent(kind="completed", provider_request_id=request_id, input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0))
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
    def __init__(self, *, api_key: str, base_url: str = "https://api.x.ai/v1"):
        super().__init__(api_key=api_key, base_url=base_url)

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        payload = {"model": model, "messages": [{"role": item["role"], "content": _openai_chat_content(item["content"])} for item in messages], "max_tokens": max_output_tokens, "stream": True, "stream_options": {"include_usage": True}}
        request_id = ""
        usage = {}
        try:
            with httpx.stream("POST", f"{self.base_url}/chat/completions", headers=self.headers, json=payload, timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS) as response:
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
                yield ProviderStreamEvent(kind="completed", provider_request_id=request_id, input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0))
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Invalid provider stream", code="invalid_stream") from exc

    def capabilities(self):
        return {"text", "streaming", "vision", "tools"}


class OpenRouterChatAdapter(XAIChatAdapter):
    def __init__(self, *, api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
        super().__init__(api_key=api_key, base_url=base_url)

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        payload = {"model": model, "messages": [{"role": item["role"], "content": _openai_chat_content(item["content"])} for item in messages], "max_tokens": max_output_tokens, "stream": True, "stream_options": {"include_usage": True}}
        request_id = ""
        usage = {}
        saw_event = False
        try:
            with httpx.stream("POST", f"{self.base_url}/chat/completions", headers=self.headers, json=payload, timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    saw_event = True
                    if isinstance(event.get("error"), dict):
                        raise _openrouter_stream_error(event)
                    request_id = event.get("id", request_id)
                    usage = event.get("usage") or usage
                    choices = event.get("choices") or []
                    if choices:
                        text = (choices[0].get("delta") or {}).get("content") or ""
                        if text:
                            yield ProviderStreamEvent(kind="delta", text_delta=text)
                if not saw_event:
                    raise ProviderError("OpenRouter stream ended without events", code="openrouter_empty_stream", retryable=True)
                yield ProviderStreamEvent(kind="completed", provider_request_id=request_id, input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0))
        except httpx.HTTPStatusError as exc:
            try:
                payload = exc.response.json()
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, dict):
                    raise _openrouter_stream_error({"error": error}) from exc
            except ProviderError:
                raise
            except Exception:
                pass
            raise _http_error(exc) from exc
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("Invalid OpenRouter stream", code="openrouter_invalid_stream") from exc

    def health_check(self):
        return self._health_get(url=f"{self.base_url}/key", headers=self.headers)


class GeminiGenerateContentAdapter(HTTPAdapter):
    def __init__(self, *, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta"):
        if not api_key:
            raise ProviderError("Provider credential is not configured", code="credential_missing", retryable=False)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @property
    def headers(self):
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        system = "\n\n".join(_text_only(item["content"]) for item in messages if item["role"] == "system")
        contents = [{"role": "model" if item["role"] == "assistant" else "user", "parts": _gemini_parts(item["content"])} for item in messages if item["role"] in {"user", "assistant"}]
        payload = {"contents": contents, "generationConfig": {"maxOutputTokens": max_output_tokens}}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        request_id = ""
        usage = {}
        try:
            with httpx.stream("POST", f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse", headers=self.headers, json=payload, timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS) as response:
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
                        raise ProviderError("Provider blocked prompt", code=f"blocked_{blocked.lower()}", retryable=False)
                    candidates = event.get("candidates") or []
                    if not candidates:
                        continue
                    for part in ((candidates[0].get("content") or {}).get("parts") or []):
                        if not part.get("thought") and part.get("text"):
                            yield ProviderStreamEvent(kind="delta", text_delta=part["text"])
                yield ProviderStreamEvent(kind="completed", provider_request_id=request_id, input_tokens=usage.get("promptTokenCount", 0), output_tokens=usage.get("candidatesTokenCount", 0))
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
    for event in adapter.stream(model=model, messages=messages, max_output_tokens=max_output_tokens):
        if event.kind == "delta":
            text += event.text_delta
        else:
            completed = event
    if completed is None:
        raise ProviderError("Provider stream ended without completion event", code="invalid_stream")
    return ProviderResult(text=text, input_tokens=completed.input_tokens, output_tokens=completed.output_tokens, provider_request_id=completed.provider_request_id)


def adapter_for(model: AIModel):
    provider = model.provider
    if provider.adapter_type == Provider.AdapterType.ECHO:
        return EchoProviderAdapter()
    api_key = provider.get_api_key()
    if provider.slug == "gigachat":
        from .gigachat_adapter import GigaChatAPIAdapter
        return GigaChatAPIAdapter(
            authorization_key=api_key,
            base_url=provider.api_base_url or os.getenv("GIGACHAT_API_BASE_URL", "https://api.giga.chat/v1"),
            scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        )
    if provider.slug == "openrouter":
        return OpenRouterChatAdapter(api_key=api_key, base_url=provider.api_base_url or os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai/api/v1"))
    if provider.adapter_type == Provider.AdapterType.OPENAI_RESPONSES:
        return OpenAIResponsesAdapter(api_key=api_key, base_url=provider.api_base_url or os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"))
    if provider.adapter_type == Provider.AdapterType.ANTHROPIC_MESSAGES:
        return AnthropicMessagesAdapter(api_key=api_key, base_url=provider.api_base_url or os.getenv("ANTHROPIC_API_BASE_URL", "https://api.anthropic.com/v1"))
    if provider.adapter_type == Provider.AdapterType.DEEPSEEK_CHAT:
        return DeepSeekChatAdapter(api_key=api_key, base_url=provider.api_base_url or os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com"))
    if provider.adapter_type == Provider.AdapterType.GEMINI_GENERATE_CONTENT:
        return GeminiGenerateContentAdapter(api_key=api_key, base_url=provider.api_base_url or os.getenv("GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"))
    if provider.adapter_type == Provider.AdapterType.XAI_CHAT:
        return XAIChatAdapter(api_key=api_key, base_url=provider.api_base_url or os.getenv("XAI_API_BASE_URL", "https://api.x.ai/v1"))
    raise ProviderError(f"Unsupported adapter: {provider.adapter_type}", code="unsupported_adapter", retryable=False)
