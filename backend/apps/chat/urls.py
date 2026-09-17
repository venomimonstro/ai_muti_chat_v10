from django.urls import path
from rest_framework.routers import DefaultRouter

from .message_actions import EditMessageView, RegenerateMessageView
from .views import ConversationViewSet

router = DefaultRouter()
router.register("conversations", ConversationViewSet, basename="conversation")

urlpatterns = [
    path(
        "conversations/<uuid:conversation_id>/messages/<uuid:message_id>/edit/",
        EditMessageView.as_view(),
        name="message-edit",
    ),
    path(
        "conversations/<uuid:conversation_id>/messages/<uuid:message_id>/regenerate/",
        RegenerateMessageView.as_view(),
        name="message-regenerate",
    ),
    *router.urls,
]
