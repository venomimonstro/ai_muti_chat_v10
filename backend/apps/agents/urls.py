from django.urls import path
from rest_framework.routers import DefaultRouter

from .config_views import AgentConfigView, AgentVersionListView, AgentVersionRestoreView
from .schedule_views import AgentScheduleViewSet
from .team_builder_views import AgentTeamBootstrapView
from .team_config_views import AgentTeamDirectorView, AgentTeamMemberDetailView
from .views import AgentRunViewSet, AgentTeamViewSet, AgentViewSet

router = DefaultRouter()
router.register("agents", AgentViewSet, basename="agent")
router.register("agent-teams", AgentTeamViewSet, basename="agent-team")
router.register("agent-runs", AgentRunViewSet, basename="agent-run")
router.register("agent-schedules", AgentScheduleViewSet, basename="agent-schedule")

urlpatterns = [
    path("agents/<uuid:agent_id>/config/", AgentConfigView.as_view(), name="agent-config"),
    path("agents/<uuid:agent_id>/versions/", AgentVersionListView.as_view(), name="agent-versions"),
    path(
        "agents/<uuid:agent_id>/versions/<uuid:version_id>/restore/",
        AgentVersionRestoreView.as_view(),
        name="agent-version-restore",
    ),
    path("agent-teams/bootstrap/", AgentTeamBootstrapView.as_view(), name="agent-team-bootstrap"),
    path(
        "agent-teams/<uuid:team_id>/members/<uuid:member_id>/",
        AgentTeamMemberDetailView.as_view(),
        name="agent-team-member-detail",
    ),
    path(
        "agent-teams/<uuid:team_id>/director/",
        AgentTeamDirectorView.as_view(),
        name="agent-team-director",
    ),
    *router.urls,
]
