from rest_framework.routers import DefaultRouter

from .smm_views import SMMContentItemViewSet, SMMContentPlanViewSet
from .views import AgentConnectionBindingViewSet, ExternalConnectionViewSet

router = DefaultRouter()
router.register("connections", ExternalConnectionViewSet, basename="external-connection")
router.register("agent-connections", AgentConnectionBindingViewSet, basename="agent-connection")
router.register("smm/plans", SMMContentPlanViewSet, basename="smm-plan")
router.register("smm/items", SMMContentItemViewSet, basename="smm-item")

urlpatterns = router.urls
