from django.urls import path

from .analytics_views import ProductAnalyticsView
from .compliance_views import ComplianceConsoleView
from .drill_views import OperationalDrillStatusView
from .growth_views import GrowthFunnelView
from .metrics_views import OperationalMetricsView
from .setup_views import CommercialProviderHealthView, CommercialSetupView
from .support_views import CategorizedSupportControlView
from .user_views import AdminUserActionView, AdminUserDetailView
from .views import (
    AuditView,
    BackupActionView,
    BackupView,
    ExecutiveOverviewView,
    FeatureFlagDetailView,
    FeatureFlagView,
    FinanceControlView,
    IncidentControlView,
    LedgerInspectorView,
    PaymentInspectorView,
    PricingControlView,
    ProviderBulkActionView,
    ProviderControlView,
    QualityControlView,
    ReleaseRolloutView,
    ReleaseView,
    RequestInspectorView,
    SecurityEventActionView,
    SecurityEventView,
    StatusIncidentControlView,
    StatusIncidentUpdateView,
    SupportStatusView,
    UserOrganizationView,
)

urlpatterns = [
    path("overview/", ExecutiveOverviewView.as_view(), name="admin-overview"),
    path("metrics/", OperationalMetricsView.as_view(), name="admin-metrics"),
    path("growth/", GrowthFunnelView.as_view(), name="admin-growth"),
    path("analytics/", ProductAnalyticsView.as_view(), name="admin-analytics"),
    path("drills/", OperationalDrillStatusView.as_view(), name="admin-drills"),
    path("finance/", FinanceControlView.as_view(), name="admin-finance"),
    path("payments/", PaymentInspectorView.as_view(), name="admin-payments"),
    path("ledger/", LedgerInspectorView.as_view(), name="admin-ledger"),
    path("pricing/", PricingControlView.as_view(), name="admin-pricing"),
    path("quality/", QualityControlView.as_view(), name="admin-quality"),
    path("incidents/", IncidentControlView.as_view(), name="admin-incidents"),
    path("providers/", ProviderControlView.as_view(), name="admin-providers"),
    path("commercial-setup/", CommercialSetupView.as_view(), name="admin-commercial-setup"),
    path(
        "commercial-setup/providers/<slug:provider_slug>/health/",
        CommercialProviderHealthView.as_view(),
        name="admin-commercial-provider-health",
    ),
    path("providers/bulk-action/", ProviderBulkActionView.as_view(), name="admin-provider-bulk-action"),
    path("requests/", RequestInspectorView.as_view(), name="admin-requests"),
    path("users-organizations/", UserOrganizationView.as_view(), name="admin-users-orgs"),
    path("users/<uuid:user_id>/", AdminUserDetailView.as_view(), name="admin-user-detail"),
    path("users/<uuid:user_id>/action/", AdminUserActionView.as_view(), name="admin-user-action"),
    path("security/", SecurityEventView.as_view(), name="admin-security"),
    path("security/<uuid:event_id>/action/", SecurityEventActionView.as_view(), name="admin-security-action"),
    path("releases/", ReleaseView.as_view(), name="admin-releases"),
    path("releases/<uuid:release_id>/rollout/", ReleaseRolloutView.as_view(), name="admin-release-rollout"),
    path("backups/", BackupView.as_view(), name="admin-backups"),
    path("backups/<uuid:backup_id>/action/", BackupActionView.as_view(), name="admin-backup-action"),
    path("support/", CategorizedSupportControlView.as_view(), name="admin-support"),
    path("support/<uuid:support_id>/status/", SupportStatusView.as_view(), name="admin-support-status"),
    path("feature-flags/", FeatureFlagView.as_view(), name="admin-feature-flags"),
    path("feature-flags/<slug:key>/", FeatureFlagDetailView.as_view(), name="admin-feature-flag-detail"),
    path("audit/", AuditView.as_view(), name="admin-audit"),
    path("signoffs/", ComplianceConsoleView.as_view(), name="admin-signoffs"),
    path("status-incidents/", StatusIncidentControlView.as_view(), name="admin-status-incidents"),
    path("status-incidents/<uuid:incident_id>/", StatusIncidentUpdateView.as_view(), name="admin-status-incident-update"),
]
