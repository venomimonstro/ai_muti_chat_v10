from rest_framework import serializers

from .models import AgentConnectionBinding, ExternalConnection
from .wordpress import normalize_wordpress_url


class ExternalConnectionSerializer(serializers.ModelSerializer):
    secret = serializers.CharField(write_only=True, required=False, allow_blank=False, trim_whitespace=True)
    secret_configured = serializers.SerializerMethodField()

    class Meta:
        model = ExternalConnection
        fields = [
            "id", "kind", "name", "base_url", "username", "secret", "secret_configured",
            "enabled", "health_state", "last_error", "last_checked_at", "metadata", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "health_state", "last_error", "last_checked_at", "metadata", "created_at", "updated_at"]

    def get_secret_configured(self, obj):
        return bool(obj.secret_encrypted)

    def validate_kind(self, value):
        if value != ExternalConnection.Kind.WORDPRESS:
            raise serializers.ValidationError("Этот тип подключения пока не поддерживается")
        return value

    def validate_base_url(self, value):
        return normalize_wordpress_url(value)

    def validate(self, attrs):
        instance = self.instance
        username = str(attrs.get("username", getattr(instance, "username", "")) or "").strip()
        has_secret = bool(str(attrs.get("secret") or "").strip()) or bool(instance and instance.secret_encrypted)
        if (attrs.get("kind") or getattr(instance, "kind", None)) == ExternalConnection.Kind.WORDPRESS:
            if not username:
                raise serializers.ValidationError({"username": "Укажите пользователя WordPress"})
            if not has_secret:
                raise serializers.ValidationError({"secret": "Укажите Application Password WordPress"})
        return attrs

    def create(self, validated_data):
        secret = validated_data.pop("secret", "")
        connection = ExternalConnection(owner=self.context["request"].user, **validated_data)
        connection.set_secret(secret)
        connection.full_clean()
        connection.save()
        return connection

    def update(self, instance, validated_data):
        secret = validated_data.pop("secret", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if secret:
            instance.set_secret(secret)
        instance.health_state = ExternalConnection.Health.UNKNOWN if instance.enabled else ExternalConnection.Health.DISABLED
        instance.last_error = ""
        instance.full_clean()
        instance.save()
        return instance


class AgentConnectionBindingSerializer(serializers.ModelSerializer):
    connection_name = serializers.CharField(source="connection.name", read_only=True)
    connection_kind = serializers.CharField(source="connection.kind", read_only=True)
    connection_health = serializers.CharField(source="connection.health_state", read_only=True)

    class Meta:
        model = AgentConnectionBinding
        fields = [
            "id", "agent", "connection", "connection_name", "connection_kind", "connection_health",
            "purpose", "enabled", "created_at",
        ]
        read_only_fields = ["id", "connection_name", "connection_kind", "connection_health", "created_at"]

    def validate(self, attrs):
        user = self.context["request"].user
        agent = attrs.get("agent") or getattr(self.instance, "agent", None)
        connection = attrs.get("connection") or getattr(self.instance, "connection", None)
        if agent and agent.owner_id != user.id:
            raise serializers.ValidationError({"agent": "Агент недоступен"})
        if connection and connection.owner_id != user.id:
            raise serializers.ValidationError({"connection": "Подключение недоступно"})
        if connection and not connection.enabled:
            raise serializers.ValidationError({"connection": "Подключение отключено"})
        return attrs
