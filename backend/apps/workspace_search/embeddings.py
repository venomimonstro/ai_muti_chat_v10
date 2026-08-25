import hashlib
import math
import re

from django.conf import settings
from django.utils import timezone

WORD_RE = re.compile(r"[a-zа-яё0-9]{2,}", re.IGNORECASE)
DIMENSIONS = 384


def _features(value: str):
    words = WORD_RE.findall(value.casefold())
    yield from (f"w:{word}" for word in words)
    yield from (f"b:{left}_{right}" for left, right in zip(words, words[1:], strict=False))
    for word in words:
        padded = f"^{word}$"
        yield from (f"c:{padded[index : index + 3]}" for index in range(len(padded) - 2))


def embed_history(value: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for feature in _features(value):
        digest = hashlib.sha256(feature.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(item * item for item in vector))
    return [item / norm for item in vector] if norm else vector


def cosine_similarity(left, right) -> float:
    if left is None or right is None:
        return 0.0
    left, right = list(left), list(right)
    if not left or not right:
        return 0.0
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if not left_norm or not right_norm:
        return 0.0
    value = sum(a * b for a, b in zip(left, right, strict=False)) / (left_norm * right_norm)
    return max(0.0, value)


def index_message(message, *, save=True):
    message.content_sha256 = hashlib.sha256(message.content.encode()).hexdigest()
    message.embedding = embed_history(message.content) if message.content else None
    message.embedding_model = settings.HISTORY_EMBEDDING_MODEL if message.content else ""
    message.indexed_at = timezone.now()
    if save:
        message.save(update_fields=["content_sha256", "embedding", "embedding_model", "indexed_at"])
    return message
