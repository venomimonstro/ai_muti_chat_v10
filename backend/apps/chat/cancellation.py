from __future__ import annotations

import hashlib

from django.core.cache import cache

CANCEL_TTL_SECONDS = 15 * 60


def _hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _idempotency_key(value: str) -> str:
    return f"chat:cancel:idem:{_hash(value)}"


def _generation_key(value) -> str:
    return f"chat:cancel:generation:{value}"


def request_cancel(*, idempotency_key: str, generation_id=None) -> None:
    key = str(idempotency_key or "").strip()
    if key:
        cache.set(_idempotency_key(key), "1", timeout=CANCEL_TTL_SECONDS)
    if generation_id:
        cache.set(_generation_key(generation_id), "1", timeout=CANCEL_TTL_SECONDS)


def cancel_requested(generation) -> bool:
    if cache.get(_generation_key(generation.id)):
        return True
    key = str(getattr(generation, "idempotency_key", "") or "").strip()
    return bool(key and cache.get(_idempotency_key(key)))


def clear_cancel(generation) -> None:
    cache.delete(_generation_key(generation.id))
    key = str(getattr(generation, "idempotency_key", "") or "").strip()
    if key:
        cache.delete(_idempotency_key(key))
