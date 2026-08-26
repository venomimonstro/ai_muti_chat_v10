from django.urls import path

from .management_views import (
    OrganizationKeyListCreateView,
    OrganizationKeyRevokeView,
    OrganizationKeyRotateView,
    OrganizationListCreateView,
    OrganizationUsageView,
)

urlpatterns = [
    path("organizations/", OrganizationListCreateView.as_view(), name="organization-list"),
    path(
        "organizations/<uuid:organization_id>/keys/",
        OrganizationKeyListCreateView.as_view(),
        name="organization-key-list",
    ),
    path(
        "organizations/<uuid:organization_id>/keys/<uuid:key_id>/revoke/",
        OrganizationKeyRevokeView.as_view(),
        name="organization-key-revoke",
    ),
    path(
        "organizations/<uuid:organization_id>/keys/<uuid:key_id>/rotate/",
        OrganizationKeyRotateView.as_view(),
        name="organization-key-rotate",
    ),
    path(
        "organizations/<uuid:organization_id>/usage/",
        OrganizationUsageView.as_view(),
        name="organization-usage",
    ),
]
