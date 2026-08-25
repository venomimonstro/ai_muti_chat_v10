import math
import re
from dataclasses import dataclass

from django.conf import settings
from django.db import connection
from django.db.models import Q
from django.utils import timezone
from pgvector.django import CosineDistance

from apps.chat.models import Conversation, Message
from apps.files.models import FileAsset
from apps.projects.access import accessible_projects

from .embeddings import cosine_similarity, embed_history

WORD_RE = re.compile(r"[a-zа-яё0-9]{2,}", re.IGNORECASE)
VALID_TYPES = {"conversation", "message", "project", "file"}
VALID_ROLES = {choice for choice, _label in Message.Role.choices}


def terms(value: str) -> set[str]:
    return set(WORD_RE.findall(value.casefold()))


def lexical_score(value: str, query: str) -> float:
    query_terms, value_terms = terms(query), terms(value)
    if not query_terms or not value_terms:
        return 0.0
    overlap = len(query_terms & value_terms) / math.sqrt(len(query_terms) * len(value_terms))
    phrase_boost = 0.25 if query.casefold() in value.casefold() else 0.0
    return min(1.0, overlap + phrase_boost)


def recency_score(created_at) -> float:
    age_days = max(0.0, (timezone.now() - created_at).total_seconds() / 86400)
    return 1.0 / (1.0 + age_days / 30)


def excerpt(value, query, radius=90):
    normalized = value.replace("\n", " ").strip()
    position = normalized.casefold().find(query.casefold())
    if position < 0:
        query_terms = terms(query)
        position = next(
            (
                normalized.casefold().find(term)
                for term in query_terms
                if term in normalized.casefold()
            ),
            0,
        )
    start = max(position - radius, 0)
    end = min(position + len(query) + radius, len(normalized))
    return f"{'…' if start else ''}{normalized[start:end]}{'…' if end < len(normalized) else ''}"


@dataclass(frozen=True)
class SearchFilters:
    types: frozenset[str]
    project_id: str | None = None
    conversation_id: str | None = None
    role: str | None = None
    date_from: object | None = None
    date_to: object | None = None


def message_queryset(user, filters: SearchFilters):
    queryset = Message.objects.filter(conversation__owner=user).select_related("conversation")
    if filters.project_id:
        queryset = queryset.filter(conversation__project_id=filters.project_id)
    if filters.conversation_id:
        queryset = queryset.filter(conversation_id=filters.conversation_id)
    if filters.role:
        queryset = queryset.filter(role=filters.role)
    if filters.date_from:
        queryset = queryset.filter(created_at__date__gte=filters.date_from)
    if filters.date_to:
        queryset = queryset.filter(created_at__date__lte=filters.date_to)
    return queryset.exclude(content="")


def _message_results(user, query: str, filters: SearchFilters, limit: int):
    queryset = message_queryset(user, filters)
    query_vector = embed_history(query)
    scan_limit = settings.SEARCH_RETRIEVAL_SCAN_LIMIT
    query_terms = list(terms(query))[:8]
    keyword_q = Q()
    for term in query_terms:
        keyword_q |= Q(content__icontains=term)
    keyword = list(queryset.filter(keyword_q).order_by("-created_at")[:scan_limit])
    if connection.vendor == "postgresql":
        vector = list(
            queryset.exclude(embedding__isnull=True)
            .annotate(vector_distance=CosineDistance("embedding", query_vector))
            .order_by("vector_distance")[:scan_limit]
        )
    else:
        vector = list(queryset.order_by("-created_at")[:scan_limit])
    candidates = {item.id: item for item in keyword}
    candidates.update({item.id: item for item in vector})
    ranked = []
    for message in candidates.values():
        lexical = lexical_score(message.content, query)
        if connection.vendor == "postgresql" and hasattr(message, "vector_distance"):
            semantic = max(0.0, 1.0 - float(message.vector_distance))
        else:
            semantic = cosine_similarity(message.embedding, query_vector)
        if lexical <= 0 and semantic < settings.SEARCH_MIN_SEMANTIC_SCORE:
            continue
        recency = recency_score(message.created_at)
        score = (
            semantic * settings.SEARCH_VECTOR_WEIGHT
            + lexical * settings.SEARCH_LEXICAL_WEIGHT
            + recency * settings.SEARCH_RECENCY_WEIGHT
        )
        match = (
            "hybrid"
            if lexical > 0 and semantic >= settings.SEARCH_MIN_SEMANTIC_SCORE
            else "keyword"
            if lexical
            else "semantic"
        )
        ranked.append((score, match, lexical, semantic, message))
    ranked.sort(key=lambda item: (-item[0], -item[4].created_at.timestamp()))
    return [
        {
            "type": "message",
            "id": str(message.id),
            "conversation_id": str(message.conversation_id),
            "project_id": (
                str(message.conversation.project_id) if message.conversation.project_id else None
            ),
            "title": message.conversation.title,
            "excerpt": excerpt(message.content, query),
            "role": message.role,
            "created_at": message.created_at,
            "score": round(score, 4),
            "match": match,
            "navigation": {
                "conversation_id": str(message.conversation_id),
                "message_id": str(message.id),
                "anchor": f"message-{message.id}",
            },
            "signals": {
                "lexical": round(lexical, 4),
                "semantic": round(semantic, 4),
            },
        }
        for score, match, lexical, semantic, message in ranked[:limit]
    ]


def search_workspace(*, user, query: str, filters: SearchFilters, limit: int):
    projects = accessible_projects(user)
    results = []
    if "message" in filters.types:
        results.extend(_message_results(user, query, filters, limit))
    if "conversation" in filters.types and not filters.role:
        conversations = Conversation.objects.filter(owner=user)
        if filters.project_id:
            conversations = conversations.filter(project_id=filters.project_id)
        if filters.conversation_id:
            conversations = conversations.filter(pk=filters.conversation_id)
        if filters.date_from:
            conversations = conversations.filter(created_at__date__gte=filters.date_from)
        if filters.date_to:
            conversations = conversations.filter(created_at__date__lte=filters.date_to)
        for conversation in conversations.filter(title__icontains=query)[:limit]:
            results.append(
                {
                    "type": "conversation",
                    "id": str(conversation.id),
                    "conversation_id": str(conversation.id),
                    "project_id": str(conversation.project_id) if conversation.project_id else None,
                    "title": conversation.title,
                    "excerpt": conversation.title,
                    "created_at": conversation.created_at,
                    "score": 1.0,
                    "match": "keyword",
                    "navigation": {"conversation_id": str(conversation.id)},
                }
            )
    if "project" in filters.types and not filters.conversation_id and not filters.role:
        project_queryset = projects
        if filters.project_id:
            project_queryset = project_queryset.filter(pk=filters.project_id)
        if filters.date_from:
            project_queryset = project_queryset.filter(created_at__date__gte=filters.date_from)
        if filters.date_to:
            project_queryset = project_queryset.filter(created_at__date__lte=filters.date_to)
        for project in project_queryset.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )[:limit]:
            results.append(
                {
                    "type": "project",
                    "id": str(project.id),
                    "project_id": str(project.id),
                    "title": project.name,
                    "excerpt": excerpt(project.description or project.name, query),
                    "created_at": project.created_at,
                    "score": 1.0,
                    "match": "keyword",
                    "navigation": {"project_id": str(project.id)},
                }
            )
    if "file" in filters.types and not filters.conversation_id and not filters.role:
        files = FileAsset.objects.filter(
            Q(owner=user) | Q(project__in=projects), deleted_at__isnull=True
        ).distinct()
        if filters.project_id:
            files = files.filter(project_id=filters.project_id)
        if filters.date_from:
            files = files.filter(created_at__date__gte=filters.date_from)
        if filters.date_to:
            files = files.filter(created_at__date__lte=filters.date_to)
        for asset in files.filter(original_name__icontains=query)[:limit]:
            results.append(
                {
                    "type": "file",
                    "id": str(asset.id),
                    "project_id": str(asset.project_id),
                    "title": asset.original_name,
                    "excerpt": asset.get_status_display(),
                    "created_at": asset.created_at,
                    "score": 1.0,
                    "match": "keyword",
                    "navigation": {"file_id": str(asset.id), "project_id": str(asset.project_id)},
                }
            )
    results.sort(key=lambda item: (-item["score"], -item["created_at"].timestamp()))
    return results[:limit]
