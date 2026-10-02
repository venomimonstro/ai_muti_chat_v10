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
    def edit(self, *, model: str, prompt: str, size: str, quality: str, count: int, image: bytes, media_type: str): ...


class EchoImageAdapter:
    # Deterministic non-blank 64x64 PNG. It intentionally satisfies the same decoder/quality
    # contract as production results so smoke tests do not bypass image validation.
    _PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAcUlEQVR4nO3PwQ1AAAAEQTSlDbqhAuVog668aGEi2Xld7rfjtlzD6zjnb+/r/Yt/Gn6uAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAK0ArQCtAO0BWq0YfU3SNpUAAAAASUVORK5CYII="
    )

    def generate(self, *, model, prompt, size, quality, count):
        return ImageProviderResult(
            images=[ImageResult(self._PNG, "image/png", prompt) for _ in range(count)],
            provider_request_id=f"echo:{model}",
        )

    def edit(self, *, model, prompt, size, quality, count, image, media_type):
        return self.generate(model=model, prompt=prompt, size=size, quality=quality, count=count)


class OpenAIImageAdapter:
    def __init__(self, *, api_key, base_url):
        if not api_key:
            raise ImageProviderError("Provider credential is not configured", code="credential_missing")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _decode(self, payload):
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
        except ImageProviderError:
            raise
        except (KeyError, ValueError, binascii.Error) as exc:
            raise ImageProviderError("Invalid provider response", code="invalid_response") from exc
        if not images:
            raise ImageProviderError("Provider returned no image", code="invalid_response")
        return ImageProviderResult(images, str(payload.get("id", "")))

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
        except httpx.HTTPStatusError as exc:
            raise ImageProviderError(
                "Provider request failed", code=f"http_{exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ImageProviderError("Provider request failed", code="http_network") from exc
        return self._decode(payload)

    def edit(self, *, model, prompt, size, quality, count, image, media_type):
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ImageProviderError("Unsupported source image type", code="invalid_source_image")
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[media_type]
        data = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": str(count),
        }
        # Image edits are multipart. Do not set Content-Type manually: httpx must add the boundary.
        try:
            response = httpx.post(
                f"{self.base_url}/images/edits",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data=data,
                files={"image": (f"source.{extension}", image, media_type)},
                timeout=settings.AI_PROVIDER_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise ImageProviderError("Provider timeout", code="timeout") from exc
        except httpx.HTTPStatusError as exc:
            raise ImageProviderError(
                "Provider edit request failed", code=f"http_{exc.response.status_code}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ImageProviderError("Provider edit request failed", code="http_network") from exc
        return self._decode(payload)


def _detect_mime(content):
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    raise ImageProviderError("Unsupported image type", code="invalid_image")


def adapter_for(model, *, funding_account_id=None):
    if model.adapter_type == model.AdapterType.ECHO:
        return EchoImageAdapter()
    if model.adapter_type == model.AdapterType.OPENAI_IMAGES:
        # Customer image traffic follows the same strict credential contract as chat:
        # HEALTHY key only, and when procurement reserved a concrete account the
        # provider request must use that exact account's key.
        from apps.ai_registry.dispatch import select_runtime_api_key

        credential, _key_id = select_runtime_api_key(
            model.provider,
            allow_probe=False,
            touch=True,
            funding_account_id=funding_account_id,
            require_funding_balance=funding_account_id is None,
        )
        return OpenAIImageAdapter(
            api_key=credential,
            base_url=model.provider.api_base_url
            or os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"),
        )
    raise ImageProviderError("Unsupported image adapter", code="unsupported_adapter")
