from rest_framework.routers import DefaultRouter

from .views import FileAssetViewSet

router = DefaultRouter()
router.register("files", FileAssetViewSet, basename="file")

urlpatterns = router.urls
