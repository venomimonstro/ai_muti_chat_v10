from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .branches import visible_messages
from .models import Conversation
from .serializers import ConversationSerializer, MessageSerializer
from .ux_models import ConversationFolder, ConversationUIState


def active_conversations(user):
    return Conversation.objects.filter(owner=user).filter(
        Q(ui_state__isnull=True) | Q(ui_state__deleted_at__isnull=True)
    )


def _decode_workspace_cursor(raw):
    if not raw:
        return None, None
    timestamp_raw, separator, message_id = raw.partition("|")
    timestamp = parse_datetime(timestamp_raw)
    if timestamp is None:
        return None, None
    return timestamp, message_id if separator and message_id else None


def _encode_workspace_cursor(message):
    return f"{message.created_at.isoformat()}|{message.id}"


class ConversationFolderSerializer(serializers.ModelSerializer):
    conversation_count = serializers.SerializerMethodField()

    class Meta:
        model = ConversationFolder
        fields = ("id", "name", "is_pinned", "position", "conversation_count", "created_at", "updated_at")
        read_only_fields = ("id", "conversation_count", "created_at", "updated_at")

    def get_conversation_count(self, obj):
        annotated = getattr(obj, "conversation_count_value", None)
        return annotated if annotated is not None else obj.conversation_states.filter(deleted_at__isnull=True).count()


class ConversationFolderViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationFolderSerializer

    def get_queryset(self):
        return ConversationFolder.objects.filter(owner=self.request.user).annotate(
            conversation_count_value=Count(
                "conversation_states",
                filter=Q(conversation_states__deleted_at__isnull=True),
            )
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
            active_conversations(request.user)
            .select_related("ui_state")
            .order_by("-updated_at", "-id")[:limit]
        )
        result = []
        for item in rows:
            try:
                ui = item.ui_state
            except ConversationUIState.DoesNotExist:
                ui = None
            result.append(
                {
                    "id": str(item.id),
                    "title": item.title,
                    "routing_mode": item.routing_mode,
                    "selected_model": item.selected_model,
                    "project": str(item.project_id) if item.project_id else None,
                    "folder": str(ui.folder_id) if ui and ui.folder_id else None,
                    "is_pinned": bool(ui and ui.is_pinned),
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
            )
        return Response(result)


class ConversationWorkspaceView(APIView):
    def get(self, request, conversation_id):
        conversation = (
            active_conversations(request.user)
            .filter(pk=conversation_id)
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
        before_time, before_id = _decode_workspace_cursor(request.query_params.get("before"))
        if before_time is not None:
            older = Q(created_at__lt=before_time)
            if before_id:
                older |= Q(created_at=before_time, id__lt=before_id)
            queryset = queryset.filter(older)
        page = list(queryset.order_by("-created_at", "-id")[:limit])
        page.reverse()
        has_more = False
        if page:
            first = page[0]
            has_more = visible_messages(conversation).filter(
                Q(created_at__lt=first.created_at)
                | Q(created_at=first.created_at, id__lt=first.id)
            ).exists()
        return Response(
            {
                "conversation": {
                    "id": str(conversation.id),
                    "title": conversation.title,
                    "selected_model": conversation.selected_model,
                    "routing_mode": conversation.routing_mode,
                    "project": str(conversation.project_id) if conversation.project_id else None,
                    "memory_enabled": conversation.memory_enabled,
                    "active_branch": (
                        str(conversation.active_branch_id)
                        if conversation.active_branch_id
                        else None
                    ),
                    "branches": [
                        {
                            "id": str(branch.id),
                            "parent": str(branch.parent_id) if branch.parent_id else None,
                            "forked_from": (
                                str(branch.forked_from_id)
                                if branch.forked_from_id
                                else None
                            ),
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
                "next_before": _encode_workspace_cursor(page[0]) if page and has_more else None,
            }
        )


class ConversationSettingsView(APIView):
    ALLOWED_FIELDS = {
        "title",
        "selected_model",
        "routing_mode",
        "project",
        "memory_enabled",
    }

    @transaction.atomic
    def patch(self, request, conversation_id):
        # Lock only the Conversation row. active_conversations() adds a nullable
        # LEFT OUTER JOIN to ui_state, and PostgreSQL rejects SELECT ... FOR UPDATE
        # when it tries to lock the nullable side of that join.
        conversation = (
            Conversation.objects.select_for_update()
            .filter(pk=conversation_id, owner=request.user)
            .first()
        )
        if conversation is None:
            return Response({"detail": "Чат не найден"}, status=404)
        if ConversationUIState.objects.filter(
            conversation=conversation,
            deleted_at__isnull=False,
        ).exists():
            return Response({"detail": "Чат не найден"}, status=404)
        payload = {key: value for key, value in request.data.items() if key in self.ALLOWED_FIELDS}
        if not payload:
            return Response({"detail": "Нет допустимых изменений"}, status=400)
        serializer = ConversationSerializer(
            conversation,
            data=payload,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        conversation.refresh_from_db()
        return Response(
            {
                "id": str(conversation.id),
                "title": conversation.title,
                "selected_model": conversation.selected_model,
                "routing_mode": conversation.routing_mode,
                "project": str(conversation.project_id) if conversation.project_id else None,
                "memory_enabled": conversation.memory_enabled,
                "updated_at": conversation.updated_at,
            }
        )


class ConversationUIStateViewSet(viewsets.ViewSet):
    def list(self, request):
        rows = ConversationUIState.objects.filter(owner=request.user, deleted_at__isnull=True).values(
            "conversation_id", "folder_id", "is_pinned"
        )
        return Response(
            [
                {
                    "conversation_id": str(row["conversation_id"]),
                    "folder": str(row["folder_id"]) if row["folder_id"] else None,
                    "is_pinned": row["is_pinned"],
                }
                for row in rows
            ]
        )

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
                folder = ConversationFolder.objects.filter(
                    pk=folder_id, owner=request.user
                ).first()
                if folder is None:
                    return Response({"folder": "Папка не найдена"}, status=400)
                state.folder = folder
            else:
                state.folder = None
            fields.append("folder")
        if "deleted" in request.data:
            state.deleted_at = timezone.now() if bool(request.data.get("deleted")) else None
            if state.deleted_at:
                state.is_pinned = False
                state.folder = None
                fields.extend(["deleted_at", "is_pinned", "folder"])
            else:
                fields.append("deleted_at")
        if fields:
            state.save(update_fields=[*dict.fromkeys(fields), "updated_at"])
        return Response(
            {
                "conversation_id": str(conversation.id),
                "folder": str(state.folder_id) if state.folder_id else None,
                "is_pinned": state.is_pinned,
                "deleted": state.deleted_at is not None,
            }
        )
