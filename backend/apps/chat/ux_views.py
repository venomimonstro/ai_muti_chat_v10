from django.db import transaction
from django.db.models import Count
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .branches import visible_messages
from .models import Conversation
from .serializers import MessageSerializer
from .ux_models import ConversationFolder, ConversationUIState


class ConversationFolderSerializer(serializers.ModelSerializer):
    conversation_count = serializers.SerializerMethodField()

    class Meta:
        model = ConversationFolder
        fields = ("id", "name", "is_pinned", "position", "conversation_count", "created_at", "updated_at")
        read_only_fields = ("id", "conversation_count", "created_at", "updated_at")

    def get_conversation_count(self, obj):
        annotated = getattr(obj, "conversation_count_value", None)
        return annotated if annotated is not None else obj.conversation_states.count()


class ConversationFolderViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationFolderSerializer

    def get_queryset(self):
        return ConversationFolder.objects.filter(owner=self.request.user).annotate(
            conversation_count_value=Count("conversation_states")
        )

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class ConversationSummaryListView(APIView):
    def get(self, request):
        try:
            limit = min(max(int(request.query_params.get("limit", 80)), 10), 200)
        except (TypeError, ValueError):
            limit = 80
        rows = (
            Conversation.objects.filter(owner=request.user)
            .select_related("ui_state")
            .order_by("-updated_at")[:limit]
        )
        result = []
        for item in rows:
            try:
                ui = item.ui_state
            except ConversationUIState.DoesNotExist:
                ui = None
            result.append({
                "id": str(item.id),
                "title": item.title,
                "routing_mode": item.routing_mode,
                "selected_model": item.selected_model,
                "project": str(item.project_id) if item.project_id else None,
                "folder": str(ui.folder_id) if ui and ui.folder_id else None,
                "is_pinned": bool(ui and ui.is_pinned),
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            })
        return Response(result)


class ConversationWorkspaceView(APIView):
    def get(self, request, conversation_id):
        conversation = (
            Conversation.objects.filter(pk=conversation_id, owner=request.user)
            .select_related("active_branch")
            .prefetch_related("branches")
            .first()
        )
        if conversation is None:
            return Response({"detail": "Чат не найден"}, status=404)
        try:
            limit = min(max(int(request.query_params.get("limit", 60)), 20), 120)
        except (TypeError, ValueError):
            limit = 60
        queryset = visible_messages(conversation).select_related("generation_response")
        before_raw = request.query_params.get("before")
        if before_raw:
            before = parse_datetime(before_raw)
            if before is not None:
                queryset = queryset.filter(created_at__lt=before)
        page = list(queryset.order_by("-created_at")[:limit])
        page.reverse()
        has_more = False
        if page:
            has_more = visible_messages(conversation).filter(created_at__lt=page[0].created_at).exists()
        return Response({
            "conversation": {
                "id": str(conversation.id),
                "title": conversation.title,
                "selected_model": conversation.selected_model,
                "routing_mode": conversation.routing_mode,
                "project": str(conversation.project_id) if conversation.project_id else None,
                "memory_enabled": conversation.memory_enabled,
                "active_branch": str(conversation.active_branch_id) if conversation.active_branch_id else None,
                "branches": [
                    {
                        "id": str(branch.id),
                        "parent": str(branch.parent_id) if branch.parent_id else None,
                        "forked_from": str(branch.forked_from_id) if branch.forked_from_id else None,
                        "title": branch.title,
                        "created_at": branch.created_at,
                    }
                    for branch in conversation.branches.all()
                ],
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
                "messages": MessageSerializer(page, many=True).data,
            },
            "has_more": has_more,
            "next_before": page[0].created_at.isoformat() if page and has_more else None,
        })


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
