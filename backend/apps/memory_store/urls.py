from rest_framework.routers import DefaultRouter

from .views import MemoryItemViewSet

router = DefaultRouter()
router.register("memories", MemoryItemViewSet, basename="memory")

urlpatterns = router.urls
