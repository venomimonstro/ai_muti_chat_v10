from django.db import transaction
from rest_framework import serializers, status, viewsets
from rest_framework.response import Response

from .models import Conversation
from .ux_models import ConversationFolder, ConversationUIState


class ConversationFolderSerializer(serializers.ModelSerializer):
    conversation_count = serializers.SerializerMethodField()

    class Meta:
        model = ConversationFolder
        fields = ("id", "name", "is_pinned", "position", "conversation_count", "created_at", "updated_at")
        read_only_fields = ("id", "conversation_count", "created_at", "updated_at")

    def get_conversation_count(self, obj):
        return obj.conversation_states.count()


class ConversationFolderViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationFolderSerializer

    def get_queryset(self):
        return ConversationFolder.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class ConversationUIStateViewSet(viewsets.ViewSet):
    def list(self, request):
        rows = ConversationUIState.objects.filter(owner=request.user).values(
            "conversation_id", "folder_id", "is_pinned"
        )
        return Response([
            {
                "conversation_id": str(row["conversation_id"]),
                "folder": str(row["folder_id"]) if row["folder_id"] else None,
                "is_pinned": row["is_pinned"],
            }
            for row in rows
        ])

    @transaction.atomic
    def partial_update(self, request, pk=None):
        conversation = Conversation.objects.select_for_update().filter(pk=pk, owner=request.user).first()
        if conversation is None:
            return Response({"detail": "Чат не найден"}, status=status.HTTP_404_NOT_FOUND)
        state, _ = ConversationUIState.objects.select_for_update().get_or_create(
            conversation=conversation,
            defaults={"owner": request.user},
        )
        fields = []
        if "is_pinned" in request.data:
            state.is_pinned = bool(request.data.get("is_pinned"))
            fields.append("is_pinned")
        if "folder" in request.data:
            folder_id = request.data.get("folder")
            if folder_id:
                folder = ConversationFolder.objects.filter(pk=folder_id, owner=request.user).first()
                if folder is None:
                    return Response({"folder": "Папка не найдена"}, status=400)
                state.folder = folder
            else:
                state.folder = None
            fields.append("folder")
        if fields:
            state.save(update_fields=[*fields, "updated_at"])
        return Response({
            "conversation_id": str(conversation.id),
            "folder": str(state.folder_id) if state.folder_id else None,
            "is_pinned": state.is_pinned,
        })
