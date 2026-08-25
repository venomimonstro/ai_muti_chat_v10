import hashlib
import math
import re
from dataclasses import dataclass

from django.conf import settings
from django.db import connection
from django.db.models import F, QuerySet
from pgvector.django import CosineDistance

from apps.projects.access import accessible_projects

from .models import FileAsset, FileChunk

WORD_RE = re.compile(r"[a-zа-яё0-9]{2,}", re.IGNORECASE)
INJECTION_PATTERNS = (
    (
        "instruction_override",
        re.compile(
            r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|system)|"
            r"(?:игнорируй|забудь)\s+(?:все\s+)?(?:предыдущие|системные|инструкции)",
            re.IGNORECASE,
        ),
        FileChunk.InjectionRisk.BLOCKED,
    ),
    (
        "role_impersonation",
        re.compile(r"<\|(?:system|developer|assistant)\|>|\[(?:system|developer)\]", re.I),
        FileChunk.InjectionRisk.BLOCKED,
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"(?:reveal|print|return|покажи|выведи).{0,48}"
            r"(?:system prompt|api[_ -]?key|secret|token|системн\w+ промпт|ключ|секрет)",
            re.IGNORECASE | re.DOTALL,
        ),
        FileChunk.InjectionRisk.BLOCKED,
    ),
    (
        "tool_or_network_action",
        re.compile(
            r"(?:call|invoke|run|execute|send|upload|вызови|запусти|выполни|отправь)"
            r".{0,48}(?:tool|command|shell|http|url|email|инструмент|команд|почт)",
            re.IGNORECASE | re.DOTALL,
        ),
        FileChunk.InjectionRisk.BLOCKED,
    ),
    (
        "model_instruction",
        re.compile(
            r"(?:you are now|act as|instructions? for (?:the )?(?:model|assistant)|"
            r"ты теперь|действуй как|инструкци\w+ для (?:модели|ассистента))",
            re.IGNORECASE,
        ),
        FileChunk.InjectionRisk.SUSPICIOUS,
    ),
)


def terms(value: str) -> set[str]:
    return set(WORD_RE.findall(value.casefold()))


def lexical_score(value: str, query_terms: set[str]) -> float:
    value_terms = terms(value)
    if not value_terms or not query_terms:
        return 0.0
    return len(value_terms & query_terms) / math.sqrt(len(value_terms) * len(query_terms))


def embed_text(value: str) -> list[float]:
    """Deterministic, private hashing embedding used until a remote embedder is configured."""
    dimensions = settings.RAG_EMBEDDING_DIMENSIONS
    vector = [0.0] * dimensions
    tokens = WORD_RE.findall(value.casefold())
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(item * item for item in vector))
    return [item / norm for item in vector] if norm else vector


def cosine_similarity(left, right) -> float:
    if left is None or right is None:
        return 0.0
    left = list(left)
    right = list(right)
    if not left or not right:
        return 0.0
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(
        0.0,
        sum(a * b for a, b in zip(left, right, strict=False)) / (left_norm * right_norm),
    )


def detect_prompt_injection(value: str) -> tuple[str, list[str]]:
    signals = []
    risk = FileChunk.InjectionRisk.SAFE
    for name, pattern, pattern_risk in INJECTION_PATTERNS:
        if pattern.search(value):
            signals.append(name)
            if pattern_risk == FileChunk.InjectionRisk.BLOCKED:
                risk = FileChunk.InjectionRisk.BLOCKED
            elif risk == FileChunk.InjectionRisk.SAFE:
                risk = FileChunk.InjectionRisk.SUSPICIOUS
    return risk, signals


def prepare_chunk(chunk: FileChunk, asset: FileAsset) -> FileChunk:
    chunk.file = asset
    chunk.content_sha256 = hashlib.sha256(chunk.content.encode()).hexdigest()
    chunk.embedding = embed_text(chunk.content)
    chunk.embedding_model = settings.RAG_EMBEDDING_MODEL
    chunk.acl_owner_id = asset.owner_id
    chunk.acl_project_id = asset.project_id
    chunk.injection_risk, chunk.injection_signals = detect_prompt_injection(chunk.content)
    from django.utils import timezone

    chunk.indexed_at = timezone.now()
    return chunk


def authorized_chunks(user, project_id) -> QuerySet:
    """Apply tenant/project ACL before candidates reach vector or lexical ranking."""
    if not accessible_projects(user).filter(pk=project_id, archived_at__isnull=True).exists():
        return FileChunk.objects.none()
    return FileChunk.objects.select_related("file").filter(
        acl_project_id=project_id,
        acl_owner_id=F("file__owner_id"),
        file__project_id=project_id,
        file__status__in=[FileAsset.Status.READY, FileAsset.Status.PARTIAL],
        file__deleted_at__isnull=True,
    )


def citation_for(chunk: FileChunk) -> dict:
    key = f"file:{chunk.file_id}:chunk:{chunk.position}"
    return {
        "id": key,
        "file_id": str(chunk.file_id),
        "file_name": chunk.file.original_name,
        "chunk_id": str(chunk.id),
        "position": chunk.position,
        "source_location": chunk.source_location,
        "project_id": str(chunk.file.project_id),
        "content_sha256": chunk.content_sha256,
    }


@dataclass(frozen=True)
class RetrievalHit:
    chunk: FileChunk
    lexical_score: float
    vector_score: float
    score: float
    citation: dict


def retrieve_project_chunks(*, user, project_id, query: str, limit: int = 4):
    queryset = authorized_chunks(user, project_id).exclude(
        injection_risk=FileChunk.InjectionRisk.BLOCKED
    )
    query_embedding = embed_text(query)
    scan_limit = settings.SMART_CONTEXT_RETRIEVAL_SCAN_LIMIT
    if connection.vendor == "postgresql":
        candidates = list(
            queryset.exclude(embedding__isnull=True)
            .annotate(vector_distance=CosineDistance("embedding", query_embedding))
            .order_by("vector_distance")[:scan_limit]
        )
    else:
        candidates = list(queryset[:scan_limit])

    query_terms = terms(query)
    hits = []
    for chunk in candidates:
        lexical = lexical_score(chunk.content, query_terms)
        if connection.vendor == "postgresql" and hasattr(chunk, "vector_distance"):
            vector = max(0.0, 1.0 - float(chunk.vector_distance))
        else:
            vector = cosine_similarity(chunk.embedding, query_embedding)
        score = vector * settings.RAG_VECTOR_WEIGHT + lexical * settings.RAG_LEXICAL_WEIGHT
        if score >= settings.SMART_CONTEXT_MIN_RELEVANCE:
            hits.append(RetrievalHit(chunk, lexical, vector, score, citation_for(chunk)))
    hits.sort(key=lambda item: (-item.score, item.chunk.position))
    return hits[: max(1, min(limit, 20))]
