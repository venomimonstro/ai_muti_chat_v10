from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_ops.permissions import IsPlatformAdmin
from apps.chat.models import Conversation, Message

from .services import audit


MAX_TERMS = 20
MAX_RESULTS = 200
MAX_CONVERSATION_MESSAGES = 500


def _terms_from_request(request):
    values = []
    raw = request.query_params.getlist("q")
    if not raw:
        joined = request.query_params.get("terms", "")
        raw = joined.replace("\r", "\n").replace(",", "\n").split("\n")
    for value in raw:
        value = str(value or "").strip()
        if value and value not in values:
            values.append(value[:200])
        if len(values) >= MAX_TERMS:
            break
    return values


class ChatSafetySearchView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        terms = _terms_from_request(request)
        user_id = str(request.query_params.get("user_id", "")).strip()
        conversation_id = str(request.query_params.get("conversation_id", "")).strip()
        role = str(request.query_params.get("role", "user")).strip()

        queryset = Message.objects.select_related("conversation__owner").order_by("-created_at")
        if role in {Message.Role.USER, Message.Role.ASSISTANT, Message.Role.SYSTEM}:
            queryset = queryset.filter(role=role)
        if user_id:
            queryset = queryset.filter(conversation__owner_id=user_id)
        if conversation_id:
            queryset = queryset.filter(conversation_id=conversation_id)
        if terms:
            predicate = Q()
            for term in terms:
                predicate |= Q(content__icontains=term)
            queryset = queryset.filter(predicate)

        rows = []
        for message in queryset[:MAX_RESULTS]:
            matched = [term for term in terms if term.casefold() in (message.content or "").casefold()]
            rows.append(
                {
                    "message_id": message.id,
                    "conversation_id": message.conversation_id,
                    "conversation_title": message.conversation.title,
                    "user_id": message.conversation.owner_id,
                    "username": message.conversation.owner.username,
                    "email": message.conversation.owner.email,
                    "user_status": message.conversation.owner.status,
                    "role": message.role,
                    "matched_terms": matched,
                    "content": message.content,
                    "created_at": message.created_at,
                }
            )
        audit(
            request,
            "safety.chat_search",
            "messages",
            "",
            {"terms": terms, "user_id": user_id, "conversation_id": conversation_id, "results": len(rows)},
        )
        return Response(
            {
                "terms": terms,
                "count": len(rows),
                "limit": MAX_RESULTS,
                "results": rows,
            }
        )


class AdminConversationMessagesView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request, conversation_id):
        conversation = Conversation.objects.select_related("owner").filter(pk=conversation_id).first()
        if conversation is None:
            return Response({"detail": "Conversation not found"}, status=404)
        messages = list(
            Message.objects.filter(conversation=conversation)
            .order_by("created_at")
            .values("id", "role", "status", "content", "created_at")[:MAX_CONVERSATION_MESSAGES]
        )
        audit(
            request,
            "safety.conversation_view",
            "conversation",
            conversation.id,
            {"user_id": str(conversation.owner_id), "message_count": len(messages)},
        )
        return Response(
            {
                "conversation": {
                    "id": conversation.id,
                    "title": conversation.title,
                    "user_id": conversation.owner_id,
                    "username": conversation.owner.username,
                    "email": conversation.owner.email,
                    "user_status": conversation.owner.status,
                    "created_at": conversation.created_at,
                },
                "messages": messages,
                "truncated": len(messages) >= MAX_CONVERSATION_MESSAGES,
            }
        )
