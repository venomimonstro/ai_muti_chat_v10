import re
from urllib.parse import urlparse

from django.db.models import Q
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Conversation, Generation, Message
from apps.files.models import FileAsset
from apps.files.serializers import FileAssetSerializer
from apps.image_studio.models import ImageGeneration
from apps.image_studio.serializers import ImageGenerationSerializer

URL_RE = re.compile(r"https?://[^\s<>\]\[(){}\"']+", re.IGNORECASE)
VALID_KINDS = {"all", "images", "files", "links"}


def _safe_url(value):
    value = str(value or "").strip().rstrip(".,;:!?")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return value[:2048]


def _conversation_for(user, conversation_id):
    conversation = (
        Conversation.objects.filter(pk=conversation_id, owner=user)
        .filter(Q(ui_state__isnull=True) | Q(ui_state__deleted_at__isnull=True))
        .first()
    )
    if conversation is None:
        raise NotFound("Чат не найден")
    return conversation


def _chat_file_ids(user, conversation):
    ids = set()
    generations = Generation.objects.filter(
        owner=user, user_message__conversation=conversation
    ).values_list("context_snapshot", flat=True)
    for snapshot in generations.iterator():
        for item in (snapshot or {}).get("vision_assets", []):
            value = item.get("file_id")
            if value:
                ids.add(str(value))
    return ids


def _chat_links(conversation, query=""):
    query_cf = query.casefold()
    seen = set()
    items = []

    def add(url, *, title="", message_id=None, source="message"):
        safe = _safe_url(url)
        if not safe or safe in seen:
            return
        if query_cf and query_cf not in safe.casefold() and query_cf not in str(title).casefold():
            return
        seen.add(safe)
        items.append({
            "url": safe,
            "title": str(title or "")[:300],
            "message_id": str(message_id) if message_id else None,
            "source": source,
        })

    for message in Message.objects.filter(conversation=conversation).only("id", "content"):
        for match in URL_RE.findall(message.content or ""):
            add(match, message_id=message.id)

    generations = Generation.objects.filter(user_message__conversation=conversation).values_list(
        "context_snapshot", "user_message_id"
    )
    for snapshot, message_id in generations.iterator():
        for source in (snapshot or {}).get("web_sources", []):
            add(source.get("url"), title=source.get("title"), message_id=message_id, source="web_search")
    return items


class ConversationAssetsView(APIView):
    def get(self, request, conversation_id):
        conversation = _conversation_for(request.user, conversation_id)
        kind = request.query_params.get("kind", "all").strip().lower()
        if kind not in VALID_KINDS:
            raise ValidationError({"kind": "Допустимо: all, images, files, links"})
        query = request.query_params.get("q", "").strip()[:200]

        image_queryset = ImageGeneration.objects.filter(
            owner=request.user,
            conversation=conversation,
            state=ImageGeneration.State.COMPLETED,
        ).select_related("model", "model__provider", "conversation").prefetch_related("images")
        if query:
            image_queryset = image_queryset.filter(
                Q(prompt__icontains=query) | Q(model__display_name__icontains=query)
            )

        file_ids = _chat_file_ids(request.user, conversation)
        file_queryset = FileAsset.objects.filter(
            owner=request.user,
            pk__in=file_ids,
        ).exclude(status=FileAsset.Status.DELETED).prefetch_related("jobs")
        if query:
            file_queryset = file_queryset.filter(original_name__icontains=query)

        link_items = _chat_links(conversation, query=query)
        counts = {
            "images": image_queryset.count(),
            "files": file_queryset.count(),
            "links": len(link_items),
        }

        images = []
        files = []
        links = []
        if kind in {"all", "images"}:
            images = ImageGenerationSerializer(
                image_queryset[:100], many=True, context={"request": request}
            ).data
        if kind in {"all", "files"}:
            files = FileAssetSerializer(file_queryset[:100], many=True).data
        if kind in {"all", "links"}:
            links = link_items[:200]

        return Response({
            "conversation": str(conversation.id),
            "kind": kind,
            "query": query,
            "counts": counts,
            "images": images,
            "files": files,
            "links": links,
        })
