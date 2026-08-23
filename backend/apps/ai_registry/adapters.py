import json
import os
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
    pass


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


class OpenAIResponsesAdapter:
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
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise ProviderError("Provider request failed") from exc

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
    raise ProviderError(f"Unsupported adapter: {provider.adapter_type}")
