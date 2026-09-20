import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from .branches import ensure_active_branch, fork_branch, visible_messages
from .models import Conversation, ConversationBranch, Message
from .serializers import MessageSerializer
from .services import generate_reply
from .ux_models import ConversationUIState


def serialize_recent_conversation(conversation, limit=60):
    messages = list(
        visible_messages(conversation)
        .select_related("generation_response")
        .order_by("-created_at", "-id")[:limit]
    )
    messages.reverse()
    return {
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
        "messages": MessageSerializer(messages, many=True).data,
    }


class OwnedConversationAction(APIView):
    def conversation(self, request, conversation_id, *, lock=False):
        # Do not join nullable ui_state/active_branch while taking a row lock:
        # PostgreSQL rejects FOR UPDATE on the nullable side of an outer join.
        queryset = Conversation.objects.filter(pk=conversation_id, owner=request.user)
        if lock:
            queryset = queryset.select_for_update()
        conversation = queryset.first()
        if conversation is None:
            return None
        if ConversationUIState.objects.filter(
            conversation_id=conversation.id,
            deleted_at__isnull=False,
        ).exists():
            return None
        return conversation

    def idempotency_key(self, request):
        key = request.headers.get("Idempotency-Key", "")
        if not key or len(key) > 160:
            raise ValidationError("Корректный Idempotency-Key обязателен")
        return key

    def _fork_before(self, *, conversation, user, target, title):
        ordered = list(visible_messages(conversation).order_by("created_at", "id"))
        try:
            index = next(i for i, item in enumerate(ordered) if item.id == target.id)
        except StopIteration as exc:
            raise ValidationError("Сообщение не входит в активную ветку") from exc
        if index > 0:
            return fork_branch(
                conversation=conversation,
                user=user,
                source_message=ordered[index - 1],
                title=title,
            )
        active = ensure_active_branch(conversation, user)
        branch = ConversationBranch.objects.create(
            conversation=conversation,
            parent=active,
            forked_from=target,
            title=title[:160],
            inherited_message_ids=[],
            created_by=user,
        )
        conversation.active_branch = branch
        conversation.save(update_fields=["active_branch", "updated_at"])
        return branch


class EditMessageView(OwnedConversationAction):
    def post(self, request, conversation_id, message_id):
        content = str(request.data.get("content", "")).strip()
        if not content or len(content) > 100_000:
            return Response(
                {"detail": "Сообщение должно содержать от 1 до 100000 символов"},
                status=400,
            )
        try:
            key = self.idempotency_key(request)
            with transaction.atomic():
                conversation = self.conversation(request, conversation_id, lock=True)
                if conversation is None:
                    return Response({"detail": "Чат не найден"}, status=404)
                message = visible_messages(conversation).filter(
                    pk=message_id, role=Message.Role.USER
                ).first()
                if message is None:
                    return Response(
                        {"detail": "Редактировать можно только своё пользовательское сообщение"},
                        status=404,
                    )
                self._fork_before(
                    conversation=conversation,
                    user=request.user,
                    target=message,
                    title="Редактирование сообщения",
                )
            generate_reply(
                user=request.user,
                conversation=conversation,
                content=content,
                client_message_id=uuid.uuid4(),
                idempotency_key=key,
            )
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=400)
        conversation.refresh_from_db()
        return Response(serialize_recent_conversation(conversation))


class RegenerateMessageView(OwnedConversationAction):
    def post(self, request, conversation_id, message_id):
        try:
            key = self.idempotency_key(request)
            with transaction.atomic():
                conversation = self.conversation(request, conversation_id, lock=True)
                if conversation is None:
                    return Response({"detail": "Чат не найден"}, status=404)
                assistant = visible_messages(conversation).filter(
                    pk=message_id, role=Message.Role.ASSISTANT
                ).first()
                if assistant is None:
                    return Response({"detail": "Ответ не найден"}, status=404)
                ordered = list(visible_messages(conversation).order_by("created_at", "id"))
                assistant_index = next(
                    (i for i, item in enumerate(ordered) if item.id == assistant.id), -1
                )
                source = next(
                    (item for item in reversed(ordered[:assistant_index]) if item.role == Message.Role.USER),
                    None,
                )
                if source is None:
                    return Response({"detail": "Исходный запрос не найден"}, status=400)
                source_content = source.content
                self._fork_before(
                    conversation=conversation,
                    user=request.user,
                    target=source,
                    title="Новый вариант ответа",
                )
            generate_reply(
                user=request.user,
                conversation=conversation,
                content=source_content,
                client_message_id=uuid.uuid4(),
                idempotency_key=key,
            )
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=400)
        conversation.refresh_from_db()
        return Response(serialize_recent_conversation(conversation))
