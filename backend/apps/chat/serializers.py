from rest_framework import serializers

from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import provider_available

from .models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ("id", "role", "content", "status", "created_at")


class ConversationSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)

    class Meta:
        model = Conversation
        fields = ("id", "title", "selected_model", "created_at", "updated_at", "messages")
        read_only_fields = ("id", "created_at", "updated_at", "messages")

    def validate_selected_model(self, value):
        try:
            model = AIModel.objects.select_related("provider").get(slug=value, enabled=True)
        except AIModel.DoesNotExist as exc:
            raise serializers.ValidationError("Модель не найдена") from exc
        if not provider_available(model.provider):
            raise serializers.ValidationError("Модель временно недоступна")
        return value


class SendMessageSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=100_000)
    client_message_id = serializers.UUIDField()
