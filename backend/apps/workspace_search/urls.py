from django.urls import path

from .safe_views import SafeWorkspaceSearchView

urlpatterns = [path("search/", SafeWorkspaceSearchView.as_view(), name="workspace-search")]
