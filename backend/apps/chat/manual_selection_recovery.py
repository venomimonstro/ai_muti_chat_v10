from __future__ import annotations

from rest_framework import serializers as drf_serializers

from apps.ai_registry.models import AIModel

from .models import Conversation


def install(chat_serializers_module) -> None:
    serializer_cls = chat_serializers_module.ConversationSerializer
    raw_validate = serializer_cls.validate_selected_model
    if getattr(raw_validate, "_ai_workspace_manual_selection_recovery", False) is True:
        return

    def validate_selected_model(self, value):
        result = raw_validate(self, value)
        initial = getattr(self, "initial_data", {}) or {}
        mode = initial.get("routing_mode")
        if self.instance is not None or mode != Conversation.RoutingMode.MANUAL:
            return result

        # An enabled row can remain selected even if it becomes temporarily
        # unroutable: the manual continuity router will keep it as the preferred
        # primary and transparently use a healthy fallback. A row that disappeared
        # or was administratively disabled cannot be routed at all, so replace it
        # with the current fail-closed catalog default instead of persisting a chat
        # that is guaranteed to fail on its first message.
        exists = AIModel.objects.filter(slug=result, enabled=True).exists()
        if exists:
            return result
        fallback = self._default_client_model()
        if fallback is None:
            raise drf_serializers.ValidationError(
                "Выбранная модель уже недоступна и сейчас нет резервной модели. Выберите AUTO позже."
            )
        return fallback.slug

    validate_selected_model._ai_workspace_manual_selection_recovery = True
    validate_selected_model._raw_validate_selected_model = raw_validate
    serializer_cls.validate_selected_model = validate_selected_model
