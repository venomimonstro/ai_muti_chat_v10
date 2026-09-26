from django.urls import path
from rest_framework.routers import DefaultRouter

from .ai_planner_views import AgentAIPlannerCreateView, AgentAIPlannerPreviewView
from .approval_views import SafeAgentApprovalDecisionView
from .cancel_views import SafeAgentRunCancelView
from .config_views import AgentConfigView, AgentVersionListView, AgentVersionRestoreView
from .dev_bootstrap_views import DevTeamBootstrapView
from .dev_merge_views import DevRunMergePullRequestView
from .dev_pr_views import DevRunPullRequestView
from .memory_views import AgentMemoryItemView, AgentMemoryView
from .operations_views import AgentOperationsSummaryView
from .readiness_views import AgentReadinessView
from .repeat_views import AgentRunRepeatView
from .run_views import SafeAgentRunView
from .schedule_views import AgentScheduleViewSet
from .team_builder_views import AgentTeamBootstrapView
from .team_config_views import AgentTeamDirectorView, AgentTeamMemberDetailView
from .team_run_views import SafeTeamRunView, TeamReadinessView
from .usage_views import AgentUsageView
from .views import AgentRunViewSet, AgentTeamViewSet, AgentViewSet
from .webhook_views import AgentWebhookInvokeView, AgentWebhookTriggerViewSet
from .wordpress_views import AgentRunWordPressView

router = DefaultRouter()
router.register("agents", AgentViewSet, basename="agent")
router.register("agent-teams", AgentTeamViewSet, basename="agent-team")
router.register("agent-runs", AgentRunViewSet, basename="agent-run")
router.register("agent-schedules", AgentScheduleViewSet, basename="agent-schedule")
router.register("agent-webhooks", AgentWebhookTriggerViewSet, basename="agent-webhook")

urlpatterns = [
    path("agents/operations/summary/", AgentOperationsSummaryView.as_view(), name="agent-operations-summary"),
    path("agents/ai-planner/preview/", AgentAIPlannerPreviewView.as_view(), name="agent-ai-planner-preview"),
    path("agents/ai-planner/create/", AgentAIPlannerCreateView.as_view(), name="agent-ai-planner-create"),
    path("agents/<uuid:agent_id>/readiness/", AgentReadinessView.as_view(), name="agent-readiness"),
    path("agents/<uuid:agent_id>/usage/", AgentUsageView.as_view(), name="agent-usage"),
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
    path("agent-teams/<uuid:team_id>/readiness/", TeamReadinessView.as_view(), name="agent-team-readiness"),
    path("agent-teams/<uuid:team_id>/run/", SafeTeamRunView.as_view(), name="agent-team-safe-run"),
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
        "agent-webhooks/<uuid:trigger_id>/invoke/",
        AgentWebhookInvokeView.as_view(),
        name="agent-webhook-invoke",
    ),
    path(
        "agent-runs/<uuid:run_id>/cancel/",
        SafeAgentRunCancelView.as_view(),
        name="agent-safe-run-cancel",
    ),
    path(
        "agent-runs/<uuid:run_id>/approvals/<uuid:approval_id>/decision/",
        SafeAgentApprovalDecisionView.as_view(),
        name="agent-safe-approval-decision",
    ),
    path(
        "agent-runs/<uuid:run_id>/repeat/",
        AgentRunRepeatView.as_view(),
        name="agent-run-repeat",
    ),
    path(
        "agent-runs/<uuid:run_id>/pull-request/",
        DevRunPullRequestView.as_view(),
        name="agent-dev-run-pull-request",
    ),
    path(
        "agent-runs/<uuid:run_id>/pull-request/merge/",
        DevRunMergePullRequestView.as_view(),
        name="agent-dev-run-pull-request-merge",
    ),
    path(
        "agent-runs/<uuid:run_id>/wordpress/",
        AgentRunWordPressView.as_view(),
        name="agent-run-wordpress",
    ),
    *router.urls,
]
