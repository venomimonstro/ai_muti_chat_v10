from rest_framework.routers import DefaultRouter

from .smm_views import SMMContentItemViewSet, SMMContentPlanViewSet, SMMPublicationAttemptViewSet
from .views import AgentConnectionBindingViewSet, ExternalConnectionViewSet

router = DefaultRouter()
router.register("connections", ExternalConnectionViewSet, basename="external-connection")
router.register("agent-connections", AgentConnectionBindingViewSet, basename="agent-connection")
router.register("smm/plans", SMMContentPlanViewSet, basename="smm-plan")
router.register("smm/items", SMMContentItemViewSet, basename="smm-item")
router.register("smm/publications", SMMPublicationAttemptViewSet, basename="smm-publication")

urlpatterns = router.urls
