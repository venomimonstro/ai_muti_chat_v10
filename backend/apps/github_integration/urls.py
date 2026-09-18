from django.urls import path

from .views import (
    GitHubCallbackView,
    GitHubConnectView,
    GitHubDirectoryView,
    GitHubFileView,
    GitHubInstallationListView,
    GitHubProjectBindingView,
    GitHubRepositoryListView,
)

urlpatterns = [
    path("github/connect/", GitHubConnectView.as_view(), name="github-connect"),
    path("github/callback/", GitHubCallbackView.as_view(), name="github-callback"),
    path("github/installations/", GitHubInstallationListView.as_view(), name="github-installations"),
    path(
        "github/installations/<uuid:installation_id>/repositories/",
        GitHubRepositoryListView.as_view(),
        name="github-installation-repositories",
    ),
    path(
        "projects/<uuid:project_id>/github/",
        GitHubProjectBindingView.as_view(),
        name="github-project-binding",
    ),
    path(
        "projects/<uuid:project_id>/github/tree/",
        GitHubDirectoryView.as_view(),
        name="github-project-tree",
    ),
    path(
        "projects/<uuid:project_id>/github/file/",
        GitHubFileView.as_view(),
        name="github-project-file",
    ),
]
