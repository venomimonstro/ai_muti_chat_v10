from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Conversation, Generation


ACTIVE_STATES = {Generation.State.QUEUED, Generation.State.RUNNING}
TERMINAL_STATES = {
    Generation.State.COMPLETED,
    Generation.State.FAILED,
    Generation.State.CANCELLED,
}


class ConversationGenerationStatusView(APIView):
    """Resolve a browser-side pending idempotency key without starting work.

    The frontend uses this only when it has a stale/recovered pending-stream record
    for the same text. It lets a real in-flight request reconnect, while a terminal
    or never-created request cannot hijack a later intentional duplicate prompt.
    """

    def get(self, request, conversation_id):
        key = str(request.query_params.get("idempotency_key") or "").strip()
        if not key or len(key) > 160:
            raise ValidationError({"idempotency_key": "Корректный Idempotency-Key обязателен"})
        conversation = Conversation.objects.filter(
            pk=conversation_id,
            owner=request.user,
        ).only("id").first()
        if conversation is None:
            return Response({"state": "missing", "active": False, "terminal": False})
        generation = (
            Generation.objects.filter(
                owner=request.user,
                idempotency_key=key,
                user_message__conversation=conversation,
            )
            .only("id", "state", "completed_at")
            .first()
        )
        if generation is None:
            return Response({"state": "missing", "active": False, "terminal": False})
        return Response(
            {
                "generation_id": str(generation.id),
                "state": generation.state,
                "active": generation.state in ACTIVE_STATES,
                "terminal": generation.state in TERMINAL_STATES,
                "completed_at": generation.completed_at,
            }
        )
