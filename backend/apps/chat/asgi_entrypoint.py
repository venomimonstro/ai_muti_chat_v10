from __future__ import annotations

from functools import wraps

from django.core.exceptions import ValidationError
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response

from apps.ai_registry.models import AIModel

from . import asgi_stream, managed_stream


def _is_asgi_request(request) -> bool:
    raw = getattr(request, "_request", None)
    return raw is not None and hasattr(raw, "scope")


async def _async_identity_stream(stream):
    # Identity responses are local and immediate; adapting them does not perform
    # blocking provider/network work on the event loop.
    for chunk in stream:
        yield chunk


def install(view_module, streaming_module) -> None:
    """Use the native async transport in production Uvicorn/ASGI requests.

    The historical DRF action constructed a synchronous StreamingHttpResponse even
    though production runs Uvicorn. Django then had to adapt the sync iterator under
    ASGI, defeating heartbeat/token streaming. Reconnects also re-entered run() and
    received generation_in_progress instead of following durable state.

    Keep the WSGI/test-client path synchronous, while ASGI requests use exactly one
    provider producer plus read-only durable followers for idempotent reconnects.
    """
    current = view_module.ConversationViewSet.stream_messages
    if getattr(current, "_ai_workspace_native_asgi_stream", False):
        return

    @wraps(current)
    def stream_messages(self, request, pk=None):
        serializer = view_module.SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key")
        if not key or len(key) > 160:
            return Response(
                {"detail": "Idempotency-Key обязателен"}, status=status.HTTP_400_BAD_REQUEST
            )
        conversation = self.get_object()
        content = serializer.validated_data["content"]
        file_ids = serializer.validated_data.get("file_ids") or []
        identity_answer = view_module.direct_identity_answer(content, file_ids)
        try:
            if identity_answer is not None:
                generation, created = view_module.create_identity_generation(
                    user=request.user,
                    conversation=conversation,
                    content=content,
                    client_message_id=serializer.validated_data["client_message_id"],
                    idempotency_key=key,
                    answer=identity_answer,
                )
                identity = view_module.identity_sse(generation)
                stream = _async_identity_stream(identity) if _is_asgi_request(request) else identity
            else:
                generation, created = streaming_module.prepare(
                    user=request.user,
                    conversation=conversation,
                    idempotency_key=key,
                    **serializer.validated_data,
                )
                if _is_asgi_request(request):
                    stream = (
                        asgi_stream.managed_run_async(generation)
                        if created
                        else asgi_stream.follow_generation_async(generation)
                    )
                else:
                    stream = managed_stream.managed_run(generation)
        except (ValidationError, AIModel.DoesNotExist) as exc:
            raise APIValidationError({"detail": str(exc)}) from exc

        response = StreamingHttpResponse(
            stream, content_type="text/event-stream; charset=utf-8"
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response

    stream_messages._ai_workspace_native_asgi_stream = True
    stream_messages._raw_stream_messages = current
    view_module.ConversationViewSet.stream_messages = stream_messages
