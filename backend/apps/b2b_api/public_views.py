import json

from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai_registry.models import AIModel

from .authentication import APIKeyAuthentication
from .services import PublicAPIError, create_completion, require_scope, usage_summary


def error_response(error):
    return Response(
        {
            "error": {
                "message": error.message,
                "type": "invalid_request_error" if error.status_code < 500 else "api_error",
                "param": error.param,
                "code": error.code,
            }
        },
        status=error.status_code,
    )


class OpenAIAPIView(APIView):
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]
    # Public API keys have transactional per-key rate/concurrency limits.
    throttle_classes = []

    def handle_exception(self, exc):
        if isinstance(exc, PublicAPIError):
            return error_response(exc)
        if isinstance(exc, AuthenticationFailed | NotAuthenticated):
            code = getattr(exc, "get_codes", lambda: "invalid_api_key")()
            if isinstance(code, list):
                code = code[0]
            return error_response(
                PublicAPIError(str(exc.detail), code=str(code), status_code=401)
            )
        return super().handle_exception(exc)


class ModelListView(OpenAIAPIView):
    def get(self, request):
        key = request.auth
        require_scope(key, "models.read")
        queryset = AIModel.objects.filter(
            enabled=True, provider__enabled=True, provider__emergency_disabled=False
        )
        if key.allowed_models:
            queryset = queryset.filter(slug__in=key.allowed_models)
        created = int(timezone.now().timestamp())
        return Response(
            {
                "object": "list",
                "data": [
                    {
                        "id": model.slug,
                        "object": "model",
                        "created": created,
                        "owned_by": model.provider.slug,
                    }
                    for model in queryset.select_related("provider").order_by("slug")
                ],
            }
        )


def _completion_payload(usage):
    return {
        "id": usage.response_id,
        "object": "chat.completion",
        "created": int(usage.created_at.timestamp()),
        "model": usage.model.slug,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": usage.response_text, "refusal": None},
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.prompt_tokens + usage.completion_tokens,
        },
        "system_fingerprint": None,
    }


def _stream_payload(usage, include_usage=False):
    base = {
        "id": usage.response_id,
        "object": "chat.completion.chunk",
        "created": int(usage.created_at.timestamp()),
        "model": usage.model.slug,
        "system_fingerprint": None,
    }
    first = {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "logprobs": None, "finish_reason": None}]}
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"
    for offset in range(0, len(usage.response_text), 80):
        chunk = {**base, "choices": [{"index": 0, "delta": {"content": usage.response_text[offset:offset + 80]}, "logprobs": None, "finish_reason": None}]}
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    final = {**base, "choices": [{"index": 0, "delta": {}, "logprobs": None, "finish_reason": "stop"}]}
    yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
    if include_usage:
        usage_chunk = {
            **base,
            "choices": [],
            "usage": {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.prompt_tokens + usage.completion_tokens,
            },
        }
        yield f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


class ChatCompletionView(OpenAIAPIView):
    def post(self, request):
        model = request.data.get("model")
        if not isinstance(model, str) or not model:
            return error_response(PublicAPIError("model is required", param="model"))
        if request.data.get("n", 1) != 1:
            return error_response(PublicAPIError("Only n=1 is supported", param="n"))
        raw_max = request.data.get("max_completion_tokens", request.data.get("max_tokens", 1024))
        if not isinstance(raw_max, int) or isinstance(raw_max, bool) or raw_max <= 0:
            return error_response(PublicAPIError("max_completion_tokens must be a positive integer", param="max_completion_tokens"))
        try:
            idempotency_key = request.headers.get("Idempotency-Key", "")
            if len(idempotency_key) > 160:
                raise PublicAPIError(
                    "Idempotency-Key is too long",
                    code="invalid_idempotency_key",
                    param="Idempotency-Key",
                )
            result = create_completion(
                key=request.auth,
                model_slug=model,
                messages=request.data.get("messages"),
                max_tokens=raw_max,
                idempotency_key=idempotency_key,
            )
        except PublicAPIError as exc:
            return error_response(exc)
        usage = result.usage
        usage.model = usage.model
        if request.data.get("stream") is True:
            include_usage = bool((request.data.get("stream_options") or {}).get("include_usage"))
            response = StreamingHttpResponse(
                _stream_payload(usage, include_usage), content_type="text/event-stream"
            )
            response["Cache-Control"] = "no-cache"
            response["X-Accel-Buffering"] = "no"
            return response
        return Response(_completion_payload(usage), status=status.HTTP_200_OK)


class UsageView(OpenAIAPIView):
    def get(self, request):
        require_scope(request.auth, "usage.read")
        return Response(usage_summary(request.auth.organization, api_key=request.auth))
