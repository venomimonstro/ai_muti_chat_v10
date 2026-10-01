from __future__ import annotations

import uuid

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .cancellation import clear_cancel, request_cancel
from .cooperative_cancel import _cancel_before_provider
from .models import Conversation, Generation


class ConversationGenerationCancelView(APIView):
    """Request cooperative cancellation of one chat generation.

    Normal live transport cancels by the original idempotency key. After a full page
    reload the browser may no longer own that transport key, but the authenticated
    conversation history safely exposes ``generation.id``. Therefore this endpoint
    also accepts ``generation_id`` and derives the existing idempotency key only
    after tenant + conversation ownership has been verified server-side.

    If the generation is still durably QUEUED, cancellation is settled immediately.
    If the stream thread has already claimed RUNNING, the durable marker is left in
    place and only that owner thread may close its provider iterator and settle the
    partial result.
    """

    def post(self, request, conversation_id):
        conversation = Conversation.objects.filter(
            pk=conversation_id,
            owner=request.user,
        ).first()
        if conversation is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        raw_generation_id = str(request.data.get("generation_id") or "").strip()
        key = str(
            request.headers.get("Idempotency-Key")
            or request.data.get("idempotency_key")
            or ""
        ).strip()
        generation = None

        if raw_generation_id:
            try:
                generation_id = uuid.UUID(raw_generation_id)
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValidationError({"generation_id": "Некорректный Generation ID"}) from exc
            generation = (
                Generation.objects.filter(
                    pk=generation_id,
                    owner=request.user,
                    user_message__conversation=conversation,
                )
                .select_related("assistant_message")
                .first()
            )
            if generation is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
            key = generation.idempotency_key
        else:
            if not key or len(key) > 160:
                raise ValidationError(
                    {"detail": "Корректный Idempotency-Key или generation_id обязателен"}
                )
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
            finalized = _cancel_before_provider(generation, queued_only=True)
            if finalized:
                clear_cancel(generation)
            # If queued_only lost the race to RUNNING, intentionally keep the
            # marker. The stream owner will see it at the next cooperative poll.
            generation.refresh_from_db(fields=["state"])

        return Response(
            {
                "accepted": True,
                "generation_id": str(generation.id) if generation is not None else None,
                "state": generation.state if generation is not None else "pending_prepare",
            },
            status=status.HTTP_202_ACCEPTED,
        )
