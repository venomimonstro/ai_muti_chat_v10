from django.urls import path
from rest_framework.routers import DefaultRouter

from .config_views import AgentConfigView, AgentVersionListView, AgentVersionRestoreView
from .views import AgentRunViewSet, AgentTeamViewSet, AgentViewSet

router = DefaultRouter()
router.register("agents", AgentViewSet, basename="agent")
router.register("agent-teams", AgentTeamViewSet, basename="agent-team")
router.register("agent-runs", AgentRunViewSet, basename="agent-run")

urlpatterns = [
    path("agents/<uuid:agent_id>/config/", AgentConfigView.as_view(), name="agent-config"),
    path("agents/<uuid:agent_id>/versions/", AgentVersionListView.as_view(), name="agent-versions"),
    path(
        "agents/<uuid:agent_id>/versions/<uuid:version_id>/restore/",
        AgentVersionRestoreView.as_view(),
        name="agent-version-restore",
    ),
    *router.urls,
]
