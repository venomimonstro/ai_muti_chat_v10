from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .cancellation import request_cancel
from .models import Conversation, Generation


class ConversationGenerationCancelView(APIView):
    """Request cooperative cancellation of one idempotent chat generation.

    The endpoint is intentionally safe before Generation creation: the same
    idempotency key is stored as a short-lived Redis + database cancellation marker,
    closing the race where a user presses Stop while prepare() is still committing.
    """

    def post(self, request, conversation_id):
        conversation = Conversation.objects.filter(
            pk=conversation_id,
            owner=request.user,
        ).first()
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        key = str(
            request.headers.get("Idempotency-Key")
            or request.data.get("idempotency_key")
            or ""
        ).strip()
        if not key or len(key) > 160:
            raise ValidationError({"detail": "Корректный Idempotency-Key обязателен"})

        generation = (
            Generation.objects.filter(
                owner=request.user,
                idempotency_key=key,
                user_message__conversation=conversation,
            )
            .only("id", "state", "idempotency_key")
            .first()
        )
        request_cancel(
            owner_id=request.user.id,
            idempotency_key=key,
            generation_id=generation.id if generation is not None else None,
        )
        return Response(
            {
                "accepted": True,
                "generation_id": str(generation.id) if generation is not None else None,
                "state": generation.state if generation is not None else "pending_prepare",
            },
            status=status.HTTP_202_ACCEPTED,
        )
