import base64
import binascii
import os
from dataclasses import dataclass
from typing import Protocol

import httpx
from django.conf import settings


class ImageProviderError(Exception):
    def __init__(self, message, *, code="provider_error"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ImageResult:
    content: bytes
    mime_type: str
    revised_prompt: str = ""


@dataclass(frozen=True)
class ImageProviderResult:
    images: list[ImageResult]
    provider_request_id: str


class ImageProviderAdapter(Protocol):
    def generate(self, *, model: str, prompt: str, size: str, quality: str, count: int): ...


class EchoImageAdapter:
    _PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def generate(self, *, model, prompt, size, quality, count):
        return ImageProviderResult(
            images=[ImageResult(self._PNG, "image/png", prompt) for _ in range(count)],
            provider_request_id=f"echo:{model}",
        )


class OpenAIImageAdapter:
    def __init__(self, *, api_key, base_url):
        if not api_key:
            raise ImageProviderError("Provider credential is not configured", code="credential_missing")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def generate(self, *, model, prompt, size, quality, count):
        request_payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": count,
        }
        # GPT Image models always return base64 and reject response_format;
        # DALL-E needs it explicitly to avoid short-lived remote URLs.
        if model.startswith("dall-e-"):
            request_payload["response_format"] = "b64_json"
        try:
            response = httpx.post(
                f"{self.base_url}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=request_payload,
                timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise ImageProviderError("Provider timeout", code="timeout") from exc
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise ImageProviderError("Provider request failed", code=f"http_{status or 'network'}") from exc
        images = []
        try:
            for item in payload.get("data", []):
                encoded = item["b64_json"]
                if not isinstance(encoded, str) or len(encoded) > (
                    (settings.IMAGE_MAX_RESULT_BYTES + 2) // 3 * 4 + 4
                ):
                    raise ValueError("Image payload exceeds configured limit")
                content = base64.b64decode(encoded, validate=True)
                images.append(ImageResult(content, _detect_mime(content), item.get("revised_prompt", "")))
        except (KeyError, ValueError, binascii.Error) as exc:
            raise ImageProviderError("Invalid provider response", code="invalid_response") from exc
        return ImageProviderResult(images, str(payload.get("id", "")))


def _detect_mime(content):
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Unsupported image type")


def adapter_for(model):
    if model.adapter_type == model.AdapterType.ECHO:
        return EchoImageAdapter()
    if model.adapter_type == model.AdapterType.OPENAI_IMAGES:
        return OpenAIImageAdapter(
            api_key=os.getenv(model.provider.credential_env or "OPENAI_API_KEY", ""),
            base_url=model.provider.api_base_url
            or os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
        )
    raise ImageProviderError("Unsupported image adapter", code="unsupported_adapter")
