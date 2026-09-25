from django.urls import path
from rest_framework.routers import DefaultRouter

from .config_views import AgentConfigView, AgentVersionListView, AgentVersionRestoreView
from .dev_bootstrap_views import DevTeamBootstrapView
from .dev_pr_views import DevRunPullRequestView
from .memory_views import AgentMemoryItemView, AgentMemoryView
from .operations_views import AgentOperationsSummaryView
from .run_views import SafeAgentRunView
from .schedule_views import AgentScheduleViewSet
from .team_builder_views import AgentTeamBootstrapView
from .team_config_views import AgentTeamDirectorView, AgentTeamMemberDetailView
from .views import AgentRunViewSet, AgentTeamViewSet, AgentViewSet
from .wordpress_views import AgentRunWordPressView

router = DefaultRouter()
router.register("agents", AgentViewSet, basename="agent")
router.register("agent-teams", AgentTeamViewSet, basename="agent-team")
router.register("agent-runs", AgentRunViewSet, basename="agent-run")
router.register("agent-schedules", AgentScheduleViewSet, basename="agent-schedule")

urlpatterns = [
    path("agents/operations/summary/", AgentOperationsSummaryView.as_view(), name="agent-operations-summary"),
    path("agents/<uuid:agent_id>/run/", SafeAgentRunView.as_view(), name="agent-safe-run"),
    path("agents/<uuid:agent_id>/config/", AgentConfigView.as_view(), name="agent-config"),
    path("agents/<uuid:agent_id>/memory/", AgentMemoryView.as_view(), name="agent-memory"),
    path(
        "agents/<uuid:agent_id>/memory/<uuid:memory_id>/",
        AgentMemoryItemView.as_view(),
        name="agent-memory-item",
    ),
    path("agents/<uuid:agent_id>/versions/", AgentVersionListView.as_view(), name="agent-versions"),
    path(
        "agents/<uuid:agent_id>/versions/<uuid:version_id>/restore/",
        AgentVersionRestoreView.as_view(),
        name="agent-version-restore",
    ),
    path("agent-teams/bootstrap/", AgentTeamBootstrapView.as_view(), name="agent-team-bootstrap"),
    path("agent-teams/bootstrap-dev/", DevTeamBootstrapView.as_view(), name="agent-team-bootstrap-dev"),
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
    path(
        "agent-runs/<uuid:run_id>/pull-request/",
        DevRunPullRequestView.as_view(),
        name="agent-dev-run-pull-request",
    ),
    path(
        "agent-runs/<uuid:run_id>/wordpress/",
        AgentRunWordPressView.as_view(),
        name="agent-run-wordpress",
    ),
    *router.urls,
]
