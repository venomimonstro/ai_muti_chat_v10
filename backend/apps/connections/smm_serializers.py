from rest_framework import serializers

from .smm_models import SMMContentItem, SMMContentPlan, SMMPublicationAttempt


class SMMContentItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMMContentItem
        fields = [
            "id", "plan", "title", "topic", "objective", "content", "cta", "hashtags",
            "status", "scheduled_at", "published_at", "media_source", "media_generation_id",
            "media_url", "media_prompt", "media_attribution", "vk_attachment", "external_post_id",
            "publish_error", "sort_order", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "published_at", "external_post_id", "publish_error", "created_at", "updated_at"]

    def validate_plan(self, value):
        if value.owner_id != self.context["request"].user.id:
            raise serializers.ValidationError("Контент-план недоступен")
        return value

    def validate(self, attrs):
        instance = self.instance
        user = self.context["request"].user
        plan = attrs.get("plan", getattr(instance, "plan", None))
        status = attrs.get("status", getattr(instance, "status", SMMContentItem.Status.IDEA))
        scheduled_at = attrs.get("scheduled_at", getattr(instance, "scheduled_at", None))
        content = str(attrs.get("content", getattr(instance, "content", "")) or "").strip()
        media_source = attrs.get("media_source", getattr(instance, "media_source", SMMContentItem.MediaSource.NONE))
        media_generation = attrs.get("media_generation_id", getattr(instance, "media_generation_id", None))
        media_url = str(attrs.get("media_url", getattr(instance, "media_url", "")) or "").strip()
        if plan and plan.owner_id != user.id:
            raise serializers.ValidationError({"plan": "Контент-план недоступен"})
        if status in {SMMContentItem.Status.SCHEDULED, SMMContentItem.Status.PUBLISHING, SMMContentItem.Status.PUBLISHED} and not content:
            raise serializers.ValidationError({"content": "Для публикации нужен текст поста"})
        if status == SMMContentItem.Status.SCHEDULED and not scheduled_at:
            raise serializers.ValidationError({"scheduled_at": "Укажите дату и время публикации"})
        if media_generation is not None and media_generation.owner_id != user.id:
            raise serializers.ValidationError({"media_generation_id": "Изображение недоступно"})
        if media_source == SMMContentItem.MediaSource.GENERATED and media_generation is None:
            raise serializers.ValidationError({"media_generation_id": "Выберите готовую AI-генерацию"})
        if media_source == SMMContentItem.MediaSource.STOCK and not media_url:
            raise serializers.ValidationError({"media_url": "Выберите изображение фотостока"})
        return attrs


class SMMPublicationAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMMPublicationAttempt
        fields = [
            "id", "item", "idempotency_key", "state", "external_post_id",
            "error_code", "error_message", "started_at", "finished_at",
        ]
        read_only_fields = fields


class SMMContentPlanSerializer(serializers.ModelSerializer):
    items = SMMContentItemSerializer(many=True, read_only=True)
    agent_name = serializers.CharField(source="agent.name", read_only=True)
    connection_name = serializers.CharField(source="connection.name", read_only=True)
    generation_state = serializers.CharField(source="generation_run.state", read_only=True)
    generation_cost_rub = serializers.DecimalField(
        source="generation_run.cost_actual_rub", max_digits=12, decimal_places=4, read_only=True
    )

    class Meta:
        model = SMMContentPlan
        fields = [
            "id", "agent", "agent_name", "connection", "connection_name", "project", "title",
            "business_context", "goal", "audience", "tone", "period_start", "period_end",
            "status", "auto_publish", "generation_run", "generation_state", "generation_cost_rub",
            "generation_error", "items", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "agent_name", "connection_name", "generation_run", "generation_state",
            "generation_cost_rub", "generation_error", "created_at", "updated_at",
        ]

    def validate(self, attrs):
        user = self.context["request"].user
        instance = self.instance
        agent = attrs.get("agent", getattr(instance, "agent", None))
        connection = attrs.get("connection", getattr(instance, "connection", None))
        project = attrs.get("project", getattr(instance, "project", None))
        period_start = attrs.get("period_start", getattr(instance, "period_start", None))
        period_end = attrs.get("period_end", getattr(instance, "period_end", None))
        if agent and agent.owner_id != user.id:
            raise serializers.ValidationError({"agent": "SMM-агент недоступен"})
        if connection:
            if connection.owner_id != user.id:
                raise serializers.ValidationError({"connection": "VK-подключение недоступно"})
            if connection.kind != "vk":
                raise serializers.ValidationError({"connection": "Нужно подключение ВКонтакте"})
            if not connection.enabled or connection.health_state != "healthy":
                raise serializers.ValidationError({"connection": "Сначала проверьте подключение VK"})
            if not str((connection.metadata or {}).get("selected_group_id") or "").strip():
                raise serializers.ValidationError({"connection": "Выберите сообщество VK для публикаций"})
        if project and project.owner_id != user.id:
            raise serializers.ValidationError({"project": "Проект недоступен"})
        if period_start and period_end and period_end < period_start:
            raise serializers.ValidationError({"period_end": "Конец периода раньше начала"})
        return attrs

    def create(self, validated_data):
        plan = SMMContentPlan(owner=self.context["request"].user, **validated_data)
        plan.full_clean()
        plan.save()
        return plan

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.full_clean()
        instance.save()
        return instance
