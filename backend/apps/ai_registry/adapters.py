from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderResult:
    text: str
    input_tokens: int
    output_tokens: int
    provider_request_id: str


class ProviderError(Exception):
    pass


class ProviderAdapter(Protocol):
    def generate(
        self, *, model: str, messages: list[dict], max_output_tokens: int
    ) -> ProviderResult: ...


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
