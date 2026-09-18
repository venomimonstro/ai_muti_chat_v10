import os
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import FileResponse
from rest_framework import status, viewsets
from rest_framework.exceptions import APIException, ValidationError as APIValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chat.models import Conversation

from .models import GeneratedImage, ImageGeneration, ImageModel
from .serializers import ImageGenerationSerializer, ImageModelSerializer
from .services import fail_queued_generation, generate, prepare_generation, preview
from .tasks import execute_image_generation_task


class ImageProcessingUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Очередь генерации изображений временно недоступна. Баланс восстановлен."
    default_code = "image_queue_unavailable"


def _payload(request):
    return {
        "model_slug": request.data.get("model", ""),
        "prompt": request.data.get("prompt", ""),
        "size": request.data.get("size", ""),
        "quality": request.data.get("quality", ""),
        "count": request.data.get("count", 1),
    }


def _conversation(request):
    conversation_id = request.data.get("conversation")
    if not conversation_id:
        return None
    try:
        UUID(str(conversation_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise APIValidationError({"conversation": ["Некорректный идентификатор чата"]}) from exc
    conversation = Conversation.objects.filter(pk=conversation_id, owner=request.user).first()
    if conversation is None:
        raise APIValidationError({"conversation": ["Чат не найден или недоступен"]})
    return conversation


def _async_images_enabled():
    explicit = os.getenv("IMAGE_PROCESSING_ASYNC")
    if explicit is not None:
        return explicit.strip().lower() == "true"
    return os.getenv("DJANGO_DEBUG", "true").strip().lower() == "false"


class ImageModelView(APIView):
    def get(self, request):
        if not settings.IMAGES_ENABLED:
            return Response([])
        queryset = ImageModel.objects.select_related("provider").filter(
            enabled=True,
            provider__enabled=True,
            provider__emergency_disabled=False,
        )
        return Response(ImageModelSerializer(queryset, many=True).data)


class ImagePreviewView(APIView):
    def post(self, request):
        try:
            model, value, _prompt, count = preview(**_payload(request))
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        threshold = Decimal(str(settings.IMAGE_CONFIRM_THRESHOLD_RUB))
        return Response(
            {
                "model": model.slug,
                "count": count,
                "expected_cost_rub": str(value.user_charge_rub),
                "provider_cost_rub": str(value.provider_cost_rub),
                "confirmation_required": value.user_charge_rub >= threshold,
                "confirmation_threshold_rub": str(threshold),
            }
        )


class ImageGenerationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ImageGenerationSerializer

    def get_queryset(self):
        queryset = ImageGeneration.objects.filter(owner=self.request.user).select_related(
            "model", "model__provider", "conversation"
        ).prefetch_related("images")
        model = self.request.query_params.get("model")
        state_filter = self.request.query_params.get("state")
        conversation_id = self.request.query_params.get("conversation")
        if model:
            queryset = queryset.filter(model__slug=model)
        if state_filter:
            queryset = queryset.filter(state=state_filter)
        if conversation_id:
            try:
                UUID(str(conversation_id))
            except (TypeError, ValueError, AttributeError) as exc:
                raise APIValidationError({"conversation": ["Некорректный идентификатор чата"]}) from exc
            queryset = queryset.filter(conversation_id=conversation_id)
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())[:100]
        return Response(self.get_serializer(queryset, many=True).data)

    def create(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key", "")
        conversation = _conversation(request)
        confirmed = request.data.get("confirm_cost") is True
        try:
            if _async_images_enabled():
                generation, created = prepare_generation(
                    user=request.user,
                    conversation=conversation,
                    idempotency_key=key,
                    confirmed=confirmed,
                    deferred=True,
                    **_payload(request),
                )
                if created:
                    try:
                        execute_image_generation_task.delay(str(generation.id))
                    except Exception as exc:
                        fail_queued_generation(generation)
                        raise ImageProcessingUnavailable() from exc
            else:
                generation = generate(
                    user=request.user,
                    conversation=conversation,
                    idempotency_key=key,
                    confirmed=confirmed,
                    **_payload(request),
                )
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        serializer = self.get_serializer(generation)
        if generation.state in {ImageGeneration.State.QUEUED, ImageGeneration.State.RUNNING}:
            response_status = status.HTTP_202_ACCEPTED
        elif generation.state == ImageGeneration.State.COMPLETED:
            response_status = status.HTTP_201_CREATED
        else:
            response_status = status.HTTP_200_OK
        return Response(serializer.data, status=response_status)


class GeneratedImageSourceView(APIView):
    def get(self, request, image_id):
        image = GeneratedImage.objects.filter(
            pk=image_id,
            generation__owner=request.user,
            generation__state=ImageGeneration.State.COMPLETED,
        ).first()
        if image is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        response = FileResponse(image.file.open("rb"), content_type=image.mime_type)
        response["Content-Disposition"] = (
            f'inline; filename="generated-{image.id}.{image.file.name.rsplit(".", 1)[-1]}"'
        )
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        return response
