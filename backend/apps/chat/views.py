from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import StreamingHttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.ai_registry.models import AIModel

from .models import Conversation, ConversationDraft, Message
from .serializers import (
    ConversationDraftSerializer,
    ConversationSerializer,
    SendMessageSerializer,
)
from .services import generate_reply
from .streaming import prepare, run


class ConversationViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationSerializer

    def get_queryset(self):
        return Conversation.objects.filter(owner=self.request.user).prefetch_related(
            Prefetch("messages", queryset=Message.objects.select_related("generation_response"))
        )

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["get", "put", "delete"])
    def draft(self, request, pk=None):
        conversation = self.get_object()
        if request.method == "GET":
            draft = ConversationDraft.objects.filter(conversation=conversation).first()
            if draft is None:
                return Response({"content": "", "version": 0, "updated_at": None})
            return Response(ConversationDraftSerializer(draft).data)
        if request.method == "DELETE":
            ConversationDraft.objects.filter(conversation=conversation).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = ConversationDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            draft = (
                ConversationDraft.objects.select_for_update()
                .filter(conversation=conversation)
                .first()
            )
            if draft is None:
                draft = ConversationDraft.objects.create(
                    conversation=conversation,
                    content=serializer.validated_data["content"],
                )
            else:
                draft.content = serializer.validated_data["content"]
                draft.version += 1
                draft.save(update_fields=["content", "version", "updated_at"])
        return Response(ConversationDraftSerializer(draft).data)

    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key")
        if not key:
            return Response(
                {"detail": "Idempotency-Key обязателен"}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            generation = generate_reply(
                user=request.user,
                conversation=self.get_object(),
                idempotency_key=key,
                **serializer.validated_data,
            )
        except (ValidationError, AIModel.DoesNotExist) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "generation_id": generation.id,
                "state": generation.state,
                "message": ConversationSerializer(self.get_object()).data["messages"][-1],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="messages/stream")
    def stream_messages(self, request, pk=None):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key")
        if not key:
            return Response(
                {"detail": "Idempotency-Key обязателен"}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            generation, _created = prepare(
                user=request.user,
                conversation=self.get_object(),
                idempotency_key=key,
                **serializer.validated_data,
            )
        except (ValidationError, AIModel.DoesNotExist) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        response = StreamingHttpResponse(
            run(generation), content_type="text/event-stream; charset=utf-8"
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response
