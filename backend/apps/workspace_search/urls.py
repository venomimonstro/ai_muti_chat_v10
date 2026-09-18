from django.urls import path

from .conversation_assets import ConversationAssetsView
from .views import WorkspaceSearchView

urlpatterns = [
    path("search/", WorkspaceSearchView.as_view(), name="workspace-search"),
    path(
        "conversations/<uuid:conversation_id>/assets/",
        ConversationAssetsView.as_view(),
        name="conversation-assets",
    ),
]
