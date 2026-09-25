from django.urls import path

from .dev_views import GitHubRepositoryHealthView, GitHubWorkingBranchView
from .flow_views import GitHubOAuthCallbackView, GitHubSetupView
from .mutation_views import GitHubFileCreateView, GitHubFileDeleteView
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
        "projects/<uuid:project_id>/github/health/",
        github_guard(GitHubRepositoryHealthView.as_view()),
        name="github-project-health",
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
    path(
        "projects/<uuid:project_id>/github/file/create/",
        github_guard(GitHubFileCreateView.as_view(), protect_path=True),
        name="github-project-file-create",
    ),
    path(
        "projects/<uuid:project_id>/github/file/delete/",
        github_guard(GitHubFileDeleteView.as_view(), protect_path=True),
        name="github-project-file-delete",
    ),
    path(
        "projects/<uuid:project_id>/github/branches/",
        github_guard(GitHubWorkingBranchView.as_view()),
        name="github-project-branch-create",
    ),
]
