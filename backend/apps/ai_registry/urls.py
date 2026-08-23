from rest_framework.routers import DefaultRouter

from .views import AIModelViewSet

router = DefaultRouter()
router.register("models", AIModelViewSet, basename="ai-model")

urlpatterns = router.urls
