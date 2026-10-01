from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .cancellation import clear_cancel, request_cancel
from .cooperative_cancel import _cancel_before_provider
from .models import Conversation, Generation


class ConversationGenerationCancelView(APIView):
    """Request cooperative cancellation of one idempotent chat generation.

    The endpoint is intentionally safe before Generation creation: the same
    idempotency key is stored as a short-lived Redis + database cancellation marker,
    closing the race where a user presses Stop while prepare() is still committing.

    If the Generation already exists but is still QUEUED, no provider call can be in
    flight yet. In that state cancellation is completed synchronously so a reserved
    customer/provider balance is released immediately instead of waiting for stale
    recovery. RUNNING generations continue to use cooperative cancellation.
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
            .select_related("assistant_message")
            .first()
        )
        request_cancel(
            owner_id=request.user.id,
            idempotency_key=key,
            generation_id=generation.id if generation is not None else None,
        )

        if generation is not None and generation.state == Generation.State.QUEUED:
            try:
                _cancel_before_provider(generation)
            finally:
                clear_cancel(generation)
            generation.refresh_from_db(fields=["state"])

        return Response(
            {
                "accepted": True,
                "generation_id": str(generation.id) if generation is not None else None,
                "state": generation.state if generation is not None else "pending_prepare",
            },
            status=status.HTTP_202_ACCEPTED,
        )
