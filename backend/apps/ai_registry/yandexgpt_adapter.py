from __future__ import annotations

import os
import time

from .adapters import AdapterHealth, DeepSeekChatAdapter, ProviderError

DEFAULT_BASE_URL = "https://ai.api.cloud.yandex.net/v1"
DEFAULT_MODEL = "yandexgpt/latest"


def normalize_model_id(value: str, *, folder_id: str = "") -> str:
    model = str(value or "").strip() or DEFAULT_MODEL
    if model.startswith("gpt://"):
        return model
    folder = str(folder_id or os.getenv("YANDEX_CLOUD_FOLDER_ID", "")).strip()
    if not folder:
        raise ProviderError(
            "YandexGPT folder_id is not configured",
            code="yandex_folder_missing",
            retryable=False,
        )
    model = model.removeprefix("yandex:").lstrip("/")
    return f"gpt://{folder}/{model}"


class YandexGPTAdapter(DeepSeekChatAdapter):
    """Yandex Cloud OpenAI-compatible chat adapter.

    Credential selection, funding-account pinning and health admission are handled by
    the shared dispatch layer. This adapter only translates the model id and performs
    the OpenAI-compatible chat-completions call.
    """

    def __init__(
        self,
        *,
        api_key: str,
        folder_id: str = "",
        base_url: str = DEFAULT_BASE_URL,
        probe_model: str = DEFAULT_MODEL,
    ):
        super().__init__(api_key=api_key, base_url=base_url or DEFAULT_BASE_URL)
        self.folder_id = str(folder_id or "").strip()
        self.probe_model = str(probe_model or DEFAULT_MODEL).strip()

    def _model(self, value: str) -> str:
        return normalize_model_id(value, folder_id=self.folder_id)

    def stream(self, *, model: str, messages: list[dict], max_output_tokens: int):
        yield from super().stream(
            model=self._model(model),
            messages=messages,
            max_output_tokens=max_output_tokens,
        )

    def generate(self, *, model: str, messages: list[dict], max_output_tokens: int):
        return super().generate(
            model=self._model(model),
            messages=messages,
            max_output_tokens=max_output_tokens,
        )

    def health_check(self) -> AdapterHealth:
        started = time.monotonic()
        try:
            self.generate(
                model=self.probe_model,
                messages=[{"role": "user", "content": "Ответь только: OK"}],
                max_output_tokens=8,
            )
            return AdapterHealth(True, int((time.monotonic() - started) * 1000))
        except ProviderError as exc:
            return AdapterHealth(
                False,
                int((time.monotonic() - started) * 1000),
                exc.code,
            )

    def capabilities(self):
        return {"text", "streaming"}
