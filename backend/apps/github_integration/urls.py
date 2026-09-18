from django.urls import path

from .flow_views import GitHubOAuthCallbackView, GitHubSetupView
from .safety import github_guard
from .views import (
    GitHubConnectView,
    GitHubDirectoryView,
    GitHubFileView,
    GitHubInstallationListView,
    GitHubProjectBindingView,
    GitHubRepositoryListView,
)

urlpatterns = [
    path("github/connect/", github_guard(GitHubConnectView.as_view()), name="github-connect"),
    path("github/setup/", github_guard(GitHubSetupView.as_view()), name="github-setup"),
    path(
        "github/callback/",
        github_guard(GitHubOAuthCallbackView.as_view()),
        name="github-callback",
    ),
    path(
        "github/installations/",
        github_guard(GitHubInstallationListView.as_view()),
        name="github-installations",
    ),
    path(
        "github/installations/<uuid:installation_id>/repositories/",
        github_guard(GitHubRepositoryListView.as_view()),
        name="github-installation-repositories",
    ),
    path(
        "projects/<uuid:project_id>/github/",
        github_guard(GitHubProjectBindingView.as_view()),
        name="github-project-binding",
    ),
    path(
        "projects/<uuid:project_id>/github/tree/",
        github_guard(GitHubDirectoryView.as_view(), protect_path=True),
        name="github-project-tree",
    ),
    path(
        "projects/<uuid:project_id>/github/file/",
        github_guard(GitHubFileView.as_view(), protect_path=True),
        name="github-project-file",
    ),
]
