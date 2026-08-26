from django.urls import reverse
from rest_framework import serializers

from .models import GeneratedImage, ImageGeneration, ImageModel


class ImageModelSerializer(serializers.ModelSerializer):
    provider = serializers.CharField(source="provider.slug")

    class Meta:
        model = ImageModel
        fields = (
            "slug", "display_name", "provider", "supported_sizes",
            "supported_qualities", "max_images",
        )


class GeneratedImageSerializer(serializers.ModelSerializer):
    source_url = serializers.SerializerMethodField()

    class Meta:
        model = GeneratedImage
        fields = (
            "id", "position", "mime_type", "size_bytes", "sha256",
            "revised_prompt", "source_url", "created_at",
        )

    def get_source_url(self, obj):
        path = reverse("generated-image-source", kwargs={"image_id": obj.id})
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path


class ImageGenerationSerializer(serializers.ModelSerializer):
    model = serializers.CharField(source="model.slug")
    model_name = serializers.CharField(source="model.display_name")
    provider = serializers.CharField(source="model.provider.slug")
    images = GeneratedImageSerializer(many=True, read_only=True)

    class Meta:
        model = ImageGeneration
        fields = (
            "id", "model", "model_name", "provider", "prompt", "size", "quality",
            "requested_count", "actual_count", "state", "estimated_cost_rub",
            "actual_cost_rub", "error_code", "images", "created_at", "completed_at",
        )
