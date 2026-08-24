from rest_framework.routers import DefaultRouter

from .views import MemoryCandidateViewSet, MemoryItemViewSet

router = DefaultRouter()
router.register("memories", MemoryItemViewSet, basename="memory")
router.register("memory-candidates", MemoryCandidateViewSet, basename="memory-candidate")

urlpatterns = router.urls
