from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import GeneratedImageSourceView, ImageGenerationViewSet, ImageModelView, ImagePreviewView

router = DefaultRouter()
router.register("images/generations", ImageGenerationViewSet, basename="image-generation")

urlpatterns = [
    path("image-models/", ImageModelView.as_view(), name="image-model-list"),
    path("images/preview/", ImagePreviewView.as_view(), name="image-preview"),
    path("images/assets/<uuid:image_id>/source/", GeneratedImageSourceView.as_view(), name="generated-image-source"),
] + router.urls
