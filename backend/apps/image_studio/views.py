from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import FileResponse
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import GeneratedImage, ImageGeneration, ImageModel
from .serializers import ImageGenerationSerializer, ImageModelSerializer
from .services import generate, preview


def _payload(request):
    return {
        "model_slug": request.data.get("model", ""),
        "prompt": request.data.get("prompt", ""),
        "size": request.data.get("size", ""),
        "quality": request.data.get("quality", ""),
        "count": request.data.get("count", 1),
    }


class ImageModelView(APIView):
    def get(self, request):
        queryset = ImageModel.objects.select_related("provider").filter(
            enabled=True, provider__enabled=True, provider__emergency_disabled=False
        )
        return Response(ImageModelSerializer(queryset, many=True).data)


class ImagePreviewView(APIView):
    def post(self, request):
        try:
            model, value, _prompt, count = preview(**_payload(request))
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        threshold = Decimal(str(settings.IMAGE_CONFIRM_THRESHOLD_RUB))
        return Response({
            "model": model.slug,
            "count": count,
            "expected_cost_rub": str(value.user_charge_rub),
            "provider_cost_rub": str(value.provider_cost_rub),
            "confirmation_required": value.user_charge_rub >= threshold,
            "confirmation_threshold_rub": str(threshold),
        })


class ImageGenerationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ImageGenerationSerializer

    def get_queryset(self):
        queryset = ImageGeneration.objects.filter(owner=self.request.user).select_related(
            "model", "model__provider"
        ).prefetch_related("images")
        model = self.request.query_params.get("model")
        state_filter = self.request.query_params.get("state")
        if model:
            queryset = queryset.filter(model__slug=model)
        if state_filter:
            queryset = queryset.filter(state=state_filter)
        return queryset

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())[:100]
        return Response(self.get_serializer(queryset, many=True).data)

    def create(self, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key", "")
        try:
            generation = generate(
                user=request.user,
                idempotency_key=key,
                confirmed=request.data.get("confirm_cost") is True,
                **_payload(request),
            )
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        serializer = self.get_serializer(generation)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if generation.state == generation.State.COMPLETED else status.HTTP_200_OK,
        )


class GeneratedImageSourceView(APIView):
    def get(self, request, image_id):
        image = GeneratedImage.objects.filter(
            pk=image_id, generation__owner=request.user,
            generation__state=ImageGeneration.State.COMPLETED,
        ).first()
        if image is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        response = FileResponse(image.file.open("rb"), content_type=image.mime_type)
        response["Content-Disposition"] = f'inline; filename="generated-{image.id}.{image.file.name.rsplit(".", 1)[-1]}"'
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        return response
