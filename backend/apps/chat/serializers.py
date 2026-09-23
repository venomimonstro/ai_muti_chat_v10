from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import provider_available
from apps.billing.pricing import active_price, quote, require_margin
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
        return {
            "id": generation.id,
            "state": generation.state,
            "model": generation.routed_model or generation.model,
            "provider": generation.provider_slug,
            "model_version": generation.context_snapshot.get("routing", {}).get("model_version"),
            "exact_api_id": generation.context_snapshot.get("routing", {}).get("exact_api_id", ""),
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
                "components": generation.context_snapshot.get("components", []),
                "citations": generation.context_snapshot.get("citations", []),
                "vision_assets": generation.context_snapshot.get("vision_assets", []),
                "web_sources": generation.context_snapshot.get("web_sources", []),
                "web_search": generation.context_snapshot.get("web_search"),
                "dropped_or_deduplicated": generation.context_snapshot.get("dropped_or_deduplicated", 0),
                "routing": generation.context_snapshot.get("routing"),
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
        queryset = AIModel.objects.filter(enabled=True).exclude(provider__slug="gigachat").select_related(
            "provider", "current_version"
        ).order_by("provider__priority", "display_name")
        for model in queryset:
            if not model.current_version_id or not model.upstream_model.strip():
                continue
            if not provider_available(model.provider):
                continue
            try:
                price = active_price(model.slug)
                require_margin(quote(price, 1_000_000, 0, provider_slug=model.provider.slug, model_slug=model.slug))
                require_margin(quote(price, 0, 1_000_000, provider_slug=model.provider.slug, model_slug=model.slug))
            except Exception:
                continue
            return model
        return None

    def create(self, validated_data):
        mode = validated_data.get("routing_mode", Conversation.RoutingMode.MANUAL)
        selected = validated_data.get("selected_model")
        if mode == Conversation.RoutingMode.MANUAL:
            if not selected or selected == "echo-v1":
                model = self._default_client_model()
                if model is None:
                    raise serializers.ValidationError(
                        {"selected_model": "Нет подключённой модели для ручного режима. Выберите AUTO или подключите клиентскую модель."}
                    )
                validated_data["selected_model"] = model.slug
        else:
            # AUTO has exactly one source of truth: the admin tier matrix. Never
            # let a stale browser/manual selection override or block AUTO creation.
            validated_data["selected_model"] = "echo-v1"
        return super().create(validated_data)

    def validate_selected_model(self, value):
        routing_mode = self.initial_data.get("routing_mode") if hasattr(self, "initial_data") else None
        if routing_mode in {
            Conversation.RoutingMode.ECONOMY,
            Conversation.RoutingMode.BALANCED,
            Conversation.RoutingMode.MAXIMUM,
        }:
            # Ignore any stale/manual model posted by the client in AUTO mode.
            return "echo-v1"
        try:
            model = AIModel.objects.select_related("provider", "current_version").get(
                slug=value, enabled=True
            )
        except AIModel.DoesNotExist as exc:
            raise serializers.ValidationError("Модель не найдена") from exc
        if model.provider.slug == "gigachat":
            raise serializers.ValidationError("GigaChat используется только внутренним AUTO-маршрутизатором")
        if not model.current_version_id or not model.upstream_model.strip():
            raise serializers.ValidationError("Модель ещё не готова к работе")
        if not provider_available(model.provider):
            raise serializers.ValidationError("Модель временно недоступна")
        try:
            price = active_price(model.slug)
            require_margin(quote(price, 1_000_000, 0, provider_slug=model.provider.slug, model_slug=model.slug))
            require_margin(quote(price, 0, 1_000_000, provider_slug=model.provider.slug, model_slug=model.slug))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                "Для модели не настроена безопасная коммерческая цена"
            ) from exc
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
