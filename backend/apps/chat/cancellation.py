from __future__ import annotations

import hashlib
import time
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from .ux_models import ChatCancellationMarker

CANCEL_TTL_SECONDS = 15 * 60
DB_FALLBACK_POLL_SECONDS = 0.75
_DB_CHECKS: dict[str, tuple[float, bool]] = {}


def _hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _idempotency_key(owner_id, value: str) -> str:
    return f"chat:cancel:idem:{owner_id}:{_hash(value)}"


def _generation_key(value) -> str:
    return f"chat:cancel:generation:{value}"


def _cache_set(key: str) -> None:
    try:
        cache.set(key, "1", timeout=CANCEL_TTL_SECONDS)
    except Exception:
        # Database marker remains authoritative when Redis/cache is unavailable.
        pass


def _cache_get(key: str) -> bool:
    try:
        return bool(cache.get(key))
    except Exception:
        return False


def _cache_delete(key: str) -> None:
    try:
        cache.delete(key)
    except Exception:
        pass


def forget_cancel_probe(generation) -> None:
    """Drop process-local polling state after any terminal/closed stream."""
    _DB_CHECKS.pop(str(generation.id), None)


def request_cancel(*, owner_id, idempotency_key: str, generation_id=None) -> None:
    key = str(idempotency_key or "").strip()
    if not key:
        return
    now = timezone.now()
    digest = _hash(key)
    expires_at = now + timedelta(seconds=CANCEL_TTL_SECONDS)
    # Stop is a low-frequency user action, so opportunistic indexed cleanup keeps
    # the durable marker table bounded without adding another background scheduler.
    ChatCancellationMarker.objects.filter(expires_at__lte=now).delete()
    ChatCancellationMarker.objects.update_or_create(
        owner_id=owner_id,
        idempotency_hash=digest,
        defaults={"generation_id": generation_id, "expires_at": expires_at},
    )
    _cache_set(_idempotency_key(owner_id, key))
    if generation_id:
        _cache_set(_generation_key(generation_id))
        _DB_CHECKS[str(generation_id)] = (time.monotonic(), True)


def _database_cancel_requested(generation) -> bool:
    generation_id = str(generation.id)
    cached = _DB_CHECKS.get(generation_id)
    now_mono = time.monotonic()
    if cached and now_mono - cached[0] < DB_FALLBACK_POLL_SECONDS:
        return cached[1]

    key = str(getattr(generation, "idempotency_key", "") or "").strip()
    filters = Q(generation_id=generation.id)
    if key:
        filters |= Q(idempotency_hash=_hash(key))
    try:
        found = ChatCancellationMarker.objects.filter(
            owner_id=generation.owner_id,
            expires_at__gt=timezone.now(),
        ).filter(filters).exists()
    except Exception:
        # A database outage is handled elsewhere by the stream/recovery path; do not
        # turn a transient DB read failure into a false cancellation.
        found = False
    _DB_CHECKS[generation_id] = (now_mono, found)
    return found


def cancel_requested(generation) -> bool:
    if _cache_get(_generation_key(generation.id)):
        return True
    key = str(getattr(generation, "idempotency_key", "") or "").strip()
    if key and _cache_get(_idempotency_key(generation.owner_id, key)):
        return True
    return _database_cancel_requested(generation)


def clear_cancel(generation) -> None:
    _cache_delete(_generation_key(generation.id))
    key = str(getattr(generation, "idempotency_key", "") or "").strip()
    if key:
        _cache_delete(_idempotency_key(generation.owner_id, key))
    try:
        query = Q(generation_id=generation.id)
        if key:
            query |= Q(idempotency_hash=_hash(key))
        ChatCancellationMarker.objects.filter(owner_id=generation.owner_id).filter(query).delete()
    except Exception:
        # Marker expires automatically and cannot affect another tenant.
        pass
    forget_cancel_probe(generation)
