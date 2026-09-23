import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from .branches import ensure_active_branch, fork_branch, visible_messages
from .cost_preview import chat_cost_preview
from .managed_stream import managed_run
from .models import Conversation, ConversationBranch, Generation, Message
from .serializers import MessageSerializer
from .streaming import prepare
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


def _preview_payload(preview):
    return {
        "estimated_min_rub": str(preview["estimated_min_rub"]),
        "estimated_max_rub": str(preview["estimated_max_rub"]),
        "confirmation_required": preview["confirmation_required"],
        "confirmation_threshold_rub": str(preview["confirmation_threshold_rub"]),
        "selected_model": preview["selected_model"],
        "models": preview.get("models", []),
    }


def _cost_guard(request, *, user, conversation, content):
    preview = chat_cost_preview(
        user=user,
        conversation=conversation,
        content=content,
        file_ids=[],
    )
    if preview.get("blocked_by_spend_guard"):
        payload = _preview_payload(preview)
        payload.update(
            {
                "code": "spend_safety_limit",
                "detail": preview.get("spend_guard_message")
                or "Повторный запрос превышает безопасный лимит расходов. Деньги не списаны.",
            }
        )
        return Response(payload, status=409)
    if not preview["confirmation_required"]:
        return None

    confirmed = request.data.get("confirm_cost") is True
    raw_ceiling = request.data.get("confirmed_max_rub")
    try:
        ceiling = Decimal(str(raw_ceiling)) if raw_ceiling not in {None, ""} else None
    except (InvalidOperation, TypeError, ValueError):
        ceiling = None
    maximum = Decimal(str(preview["estimated_max_rub"]))
    if not confirmed or ceiling is None or ceiling < maximum:
        payload = _preview_payload(preview)
        payload.update(
            {
                "code": "cost_confirmation_required",
                "detail": (
                    "Повторная генерация создаёт новый расход LLM. "
                    "Подтвердите максимальную стоимость до запуска модели."
                ),
            }
        )
        return Response(payload, status=409)
    return None


def _action_client_message_id(user, action, key, source_message_id):
    """Stable across retries and bound to the exact source message."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"bbtec-chat-action:{user.id}:{action}:{source_message_id}:{key}",
    )


def _validate_action_replay(
    generation, *, conversation, client_message_id, content=None
):
    request_message = generation.user_message
    if (
        request_message.conversation_id != conversation.id
        or request_message.client_message_id != client_message_id
        or (content is not None and request_message.content != content)
    ):
        raise ValidationError("Idempotency-Key уже использован для другой операции")
    return generation


def _run_prepared_generation(generation, created):
    if created:
        # Use the same wrapper as the primary chat stream. It reconciles partial
        # confirmed provider usage back into Generation.actual_cost_rub even when
        # the low-level provider stream fails after delivering tokens.
        list(managed_run(generation))
        generation.refresh_from_db()
    return generation


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
            client_message_id = _action_client_message_id(request.user, "edit", key, message_id)
            with transaction.atomic():
                # Keep the same lock order as normal prepare(): user, then conversation.
                request.user.__class__.objects.select_for_update().only("pk").get(pk=request.user.pk)
                conversation = self.conversation(request, conversation_id, lock=True)
                if conversation is None:
                    return Response({"detail": "Чат не найден"}, status=404)
                existing = (
                    Generation.objects.filter(owner=request.user, idempotency_key=key)
                    .select_related("user_message")
                    .first()
                )
                if existing is not None:
                    generation = _validate_action_replay(
                        existing,
                        conversation=conversation,
                        content=content,
                        client_message_id=client_message_id,
                    )
                    created = False
                else:
                    message = visible_messages(conversation).filter(
                        pk=message_id, role=Message.Role.USER
                    ).first()
                    if message is None:
                        return Response(
                            {"detail": "Редактировать можно только своё пользовательское сообщение"},
                            status=404,
                        )
                    blocked = _cost_guard(
                        request,
                        user=request.user,
                        conversation=conversation,
                        content=content,
                    )
                    if blocked is not None:
                        return blocked
                    self._fork_before(
                        conversation=conversation,
                        user=request.user,
                        target=message,
                        title="Редактирование сообщения",
                    )
                    generation, created = prepare(
                        user=request.user,
                        conversation=conversation,
                        content=content,
                        client_message_id=client_message_id,
                        idempotency_key=key,
                        file_ids=[],
                    )
            _run_prepared_generation(generation, created)
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=400)
        conversation.refresh_from_db()
        return Response(serialize_recent_conversation(conversation))


class RegenerateMessageView(OwnedConversationAction):
    def post(self, request, conversation_id, message_id):
        try:
            key = self.idempotency_key(request)
            client_message_id = _action_client_message_id(
                request.user, "regenerate", key, message_id
            )
            with transaction.atomic():
                # Serialize same-user paid actions before any branch mutation. A retry
                # can observe the prepared Generation without needing the old branch.
                request.user.__class__.objects.select_for_update().only("pk").get(pk=request.user.pk)
                conversation = self.conversation(request, conversation_id, lock=True)
                if conversation is None:
                    return Response({"detail": "Чат не найден"}, status=404)
                existing = (
                    Generation.objects.filter(owner=request.user, idempotency_key=key)
                    .select_related("user_message")
                    .first()
                )
                if existing is not None:
                    generation = _validate_action_replay(
                        existing,
                        conversation=conversation,
                        client_message_id=client_message_id,
                    )
                    created = False
                else:
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
                    blocked = _cost_guard(
                        request,
                        user=request.user,
                        conversation=conversation,
                        content=source_content,
                    )
                    if blocked is not None:
                        return blocked
                    self._fork_before(
                        conversation=conversation,
                        user=request.user,
                        target=source,
                        title="Новый вариант ответа",
                    )
                    generation, created = prepare(
                        user=request.user,
                        conversation=conversation,
                        content=source_content,
                        client_message_id=client_message_id,
                        idempotency_key=key,
                        file_ids=[],
                    )
            _run_prepared_generation(generation, created)
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=400)
        conversation.refresh_from_db()
        return Response(serialize_recent_conversation(conversation))
