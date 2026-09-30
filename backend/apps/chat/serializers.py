from rest_framework import serializers

from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import model_client_ready
from apps.projects.access import accessible_projects

from .branches import visible_messages
from .models import Conversation, ConversationDraft, Message


PUBLIC_SYSTEM_LEVELS = {
    Conversation.RoutingMode.ECONOMY: "System Lite",
    Conversation.RoutingMode.BALANCED: "System Pro",
    Conversation.RoutingMode.MAXIMUM: "System Max",
}
INTERNAL_CONTEXT_KINDS = {"system_policy", "product_identity"}


def _public_generation_identity(generation):
    routing = generation.context_snapshot.get("routing", {}) or {}
    mode = routing.get("mode")
    raw_model = generation.routed_model or generation.model or ""
    is_internal_gigachat = generation.provider_slug == "gigachat" or raw_model.startswith("gigachat")
    if is_internal_gigachat:
        fallback = "System Pro"
        lowered = raw_model.lower()
        if "lite" in lowered:
            fallback = "System Lite"
        elif "max" in lowered:
            fallback = "System Max"
        elif "pro" in lowered:
            fallback = "System Pro"
        return PUBLIC_SYSTEM_LEVELS.get(mode, fallback), "system", True
    return raw_model, generation.provider_slug, False


def _public_routing_snapshot(generation, model_name, hide_upstream):
    routing = generation.context_snapshot.get("routing")
    if not isinstance(routing, dict):
        return routing
    if not hide_upstream:
        return routing
    return {
        "decision_id": routing.get("decision_id"),
        "mode": routing.get("mode"),
        "task_taxonomy": routing.get("task_taxonomy"),
        "selected_model": model_name,
        "model_version": model_name,
        "exact_api_id": "",
        "explanation": f"Использован уровень {model_name}.",
        "policy_version": routing.get("policy_version"),
        "classification_confidence": routing.get("classification_confidence"),
        "required_capabilities": routing.get("required_capabilities", []),
        "estimated_cost_rub": routing.get("estimated_cost_rub"),
        "candidates": [],
    }


def _public_context_components(generation):
    components = generation.context_snapshot.get("components", [])
    if not isinstance(components, list):
        return []
    return [
        item for item in components
        if not isinstance(item, dict) or item.get("kind") not in INTERNAL_CONTEXT_KINDS
    ]


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
        model_name, provider_name, hide_upstream = _public_generation_identity(generation)
        routing = _public_routing_snapshot(generation, model_name, hide_upstream)
        return {
            "id": generation.id,
            "state": generation.state,
            "model": model_name,
            "provider": provider_name,
            "model_version": model_name if hide_upstream else generation.context_snapshot.get("routing", {}).get("model_version"),
            "exact_api_id": "" if hide_upstream else generation.context_snapshot.get("routing", {}).get("exact_api_id", ""),
            "cost_rub": generation.actual_cost_rub,
            "input_tokens": generation.input_tokens,
            "output_tokens": generation.output_tokens,
            "error_code": generation.error_code,
            "correlation_id": generation.correlation_id,
            "completed_at": generation.completed_at,
            "context": {
                "memories": generation.context_snapshot.get("memory_items", []),
                "memory_action": generation.context_snapshot.get("memory_action"),
                "version": generation.context_snapshot.get("version"),
                "sha256": generation.context_snapshot.get("sha256", ""),
                "budget": generation.context_snapshot.get("budget", {}),
                "components": _public_context_components(generation),
                "citations": generation.context_snapshot.get("citations", []),
                "vision_assets": generation.context_snapshot.get("vision_assets", []),
                "web_sources": generation.context_snapshot.get("web_sources", []),
                "web_search": generation.context_snapshot.get("web_search"),
                "dropped_or_deduplicated": generation.context_snapshot.get("dropped_or_deduplicated", 0),
                "routing": routing,
            },
        }


class ConversationSerializer(serializers.ModelSerializer):
    messages = serializers.SerializerMethodField()
    branches = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = (
            "id", "title", "selected_model", "routing_mode", "project", "memory_enabled",
            "active_branch", "branches", "created_at", "updated_at", "messages",
        )
        read_only_fields = (
            "id", "active_branch", "branches", "created_at", "updated_at", "messages",
        )

    def get_messages(self, obj):
        return MessageSerializer(
            visible_messages(obj).select_related("generation_response").order_by("created_at", "id"),
            many=True,
        ).data

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

    @staticmethod
    def _default_client_model():
        queryset = AIModel.objects.filter(enabled=True).select_related(
            "provider", "current_version"
        ).order_by("provider__priority", "display_name")
        return next((model for model in queryset if model_client_ready(model)), None)

    def _normalize_legacy_new_chat_mode(self, validated_data):
        """Treat the historical web-client default as AUTO only on creation.

        Older frontend builds created every new chat as ``balanced`` and also sent
        an arbitrary selected_model even though non-manual modes ignore it. That
        signature is distinct from an intentional PATCH of an existing chat to the
        Medium level, so it can be safely normalized without changing explicit user
        choices.
        """
        initial = getattr(self, "initial_data", {}) or {}
        raw_mode = initial.get("routing_mode")
        raw_selected = initial.get("selected_model")
        raw_title = str(initial.get("title") or "").strip()
        if (
            raw_mode == Conversation.RoutingMode.BALANCED
            and raw_selected
            and raw_title in {"", "Новый чат"}
        ):
            validated_data["routing_mode"] = Conversation.RoutingMode.AUTO

    def create(self, validated_data):
        self._normalize_legacy_new_chat_mode(validated_data)
        mode = validated_data.get("routing_mode", Conversation.RoutingMode.AUTO)
        selected = validated_data.get("selected_model")
        if mode == Conversation.RoutingMode.MANUAL:
            if not selected or selected == "echo-v1":
                model = self._default_client_model()
                if model is None:
                    raise serializers.ValidationError(
                        {"selected_model": "Сейчас нет доступных моделей. Попробуйте AUTO позже."}
                    )
                validated_data["selected_model"] = model.slug
        else:
            validated_data["selected_model"] = "echo-v1"
        return super().create(validated_data)

    def validate_selected_model(self, value):
        routing_mode = self.initial_data.get("routing_mode") if hasattr(self, "initial_data") else None
        if routing_mode in {
            Conversation.RoutingMode.AUTO,
            Conversation.RoutingMode.ECONOMY,
            Conversation.RoutingMode.BALANCED,
            Conversation.RoutingMode.MAXIMUM,
        }:
            return "echo-v1"
        try:
            model = AIModel.objects.select_related("provider", "current_version").get(
                slug=value, enabled=True
            )
        except AIModel.DoesNotExist as exc:
            raise serializers.ValidationError("Модель не найдена") from exc
        if not model_client_ready(model):
            raise serializers.ValidationError(
                "Модель сейчас недоступна. Выберите другую модель или AUTO."
            )
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
    file_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True, max_length=4
    )


class ConversationDraftSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConversationDraft
        fields = ("content", "version", "updated_at")
        read_only_fields = ("version", "updated_at")
