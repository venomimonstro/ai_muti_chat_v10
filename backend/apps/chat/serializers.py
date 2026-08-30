from rest_framework import serializers

from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import provider_available
from apps.projects.access import accessible_projects

from .branches import visible_messages
from .models import Conversation, ConversationDraft, Message


class MessageSerializer(serializers.ModelSerializer):
    generation = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = ("id", "branch", "role", "content", "status", "generation", "created_at")

    def get_generation(self, obj):
        try:
            generation = obj.generation_response
        except Message.generation_response.RelatedObjectDoesNotExist:
            return None
        routing = generation.context_snapshot.get("routing", {})
        payload = {
            "id": generation.id,
            "state": generation.state,
            "model": generation.routed_model or generation.model,
            "provider": generation.provider_slug,
            "model_version": routing.get("model_version"),
            "exact_api_id": routing.get("exact_api_id", ""),
            "cost_rub": generation.actual_cost_rub,
            "input_tokens": generation.input_tokens,
            "output_tokens": generation.output_tokens,
            "error_code": generation.error_code,
            "correlation_id": generation.correlation_id,
            "completed_at": generation.completed_at,
        }
        if self.context.get("include_message_context"):
            payload["context"] = {
                "memories": generation.context_snapshot.get("memory_items", []),
                "memory_action": generation.context_snapshot.get("memory_action"),
                "version": generation.context_snapshot.get("version"),
                "sha256": generation.context_snapshot.get("sha256", ""),
                "budget": generation.context_snapshot.get("budget", {}),
                "components": generation.context_snapshot.get("components", []),
                "citations": generation.context_snapshot.get("citations", []),
                "dropped_or_deduplicated": generation.context_snapshot.get(
                    "dropped_or_deduplicated", 0
                ),
                "routing": routing,
            }
        return payload


class ConversationSummarySerializer(serializers.ModelSerializer):
    branches = serializers.SerializerMethodField()
    message_count = serializers.IntegerField(read_only=True)
    last_message_preview = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = (
            "id",
            "title",
            "selected_model",
            "routing_mode",
            "project",
            "memory_enabled",
            "active_branch",
            "branches",
            "message_count",
            "last_message_preview",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "active_branch",
            "branches",
            "message_count",
            "last_message_preview",
            "created_at",
            "updated_at",
        )

    def get_last_message_preview(self, obj):
        preview = getattr(obj, "last_message_preview", None)
        if preview is None:
            return ""
        text = str(preview).strip().replace("\n", " ")
        return text[:160]

    def get_branches(self, obj):
        return [
            {
                "id": str(branch.id),
                "parent": str(branch.parent_id) if branch.parent_id else None,
                "forked_from": str(branch.forked_from_id) if branch.forked_from_id else None,
                "title": branch.title,
                "created_at": branch.created_at,
            }
            for branch in obj.branches.all()
        ]


class ConversationSerializer(ConversationSummarySerializer):
    messages = serializers.SerializerMethodField()

    class Meta(ConversationSummarySerializer.Meta):
        fields = ConversationSummarySerializer.Meta.fields + ("messages",)
        read_only_fields = ConversationSummarySerializer.Meta.read_only_fields + ("messages",)

    def get_messages(self, obj):
        return MessageSerializer(
            visible_messages(obj).order_by("created_at"),
            many=True,
            context={**self.context, "include_message_context": self.context.get("include_message_context", False)},
        ).data

    def validate_selected_model(self, value):
        try:
            model = AIModel.objects.select_related("provider").get(slug=value, enabled=True)
        except AIModel.DoesNotExist as exc:
            raise serializers.ValidationError("Модель не найдена") from exc
        if not provider_available(model.provider):
            raise serializers.ValidationError("Модель временно недоступна")
        return value

    def validate_project(self, value):
        if value is None:
            return None
        user = self.context["request"].user
        if not accessible_projects(user, write=True).filter(pk=value.pk).exists():
            raise serializers.ValidationError("Проект не найден или недоступен")
        if value.archived_at:
            raise serializers.ValidationError("Проект находится в архиве")
        return value


class SendMessageSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=100_000)
    client_message_id = serializers.UUIDField()


class ConversationDraftSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversationDraft
        fields = ("content", "version", "updated_at")
        read_only_fields = ("version", "updated_at")
