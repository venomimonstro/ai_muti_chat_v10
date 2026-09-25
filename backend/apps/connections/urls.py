from rest_framework.routers import DefaultRouter

from .views import AgentConnectionBindingViewSet, ExternalConnectionViewSet

router = DefaultRouter()
router.register("connections", ExternalConnectionViewSet, basename="external-connection")
router.register("agent-connections", AgentConnectionBindingViewSet, basename="agent-connection")

urlpatterns = router.urls
