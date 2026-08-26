from django.urls import path

from .public_views import ChatCompletionView, ModelListView, UsageView

urlpatterns = [
    path("models", ModelListView.as_view(), name="public-model-list"),
    path("chat/completions", ChatCompletionView.as_view(), name="public-chat-completions"),
    path("usage", UsageView.as_view(), name="public-api-usage"),
]
