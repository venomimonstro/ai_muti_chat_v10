from rest_framework.routers import DefaultRouter

from .views import AgentRunViewSet, AgentTeamViewSet, AgentViewSet

router = DefaultRouter()
router.register("agents", AgentViewSet, basename="agent")
router.register("agent-teams", AgentTeamViewSet, basename="agent-team")
router.register("agent-runs", AgentRunViewSet, basename="agent-run")

urlpatterns = router.urls
