from django.urls import path

from .views import WorkspaceSearchView

urlpatterns = [path("search/", WorkspaceSearchView.as_view(), name="workspace-search")]
