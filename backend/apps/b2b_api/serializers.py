import ipaddress

from rest_framework import serializers

from .models import APIKey, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = (
            "id",
            "name",
            "slug",
            "monthly_limit_rub",
            "active",
            "role",
            "created_at",
        )
        read_only_fields = ("id", "slug", "active", "role", "created_at")

    def get_role(self, obj):
        membership = next(
            (
                item
                for item in obj.memberships.all()
                if item.user_id == self.context["request"].user.id
            ),
            None,
        )
        return membership.role if membership else None


class APIKeySerializer(serializers.ModelSerializer):
    active = serializers.SerializerMethodField()

    class Meta:
        model = APIKey
        fields = (
            "id",
            "name",
            "prefix",
            "scopes",
            "allowed_models",
            "allowed_endpoints",
            "monthly_limit_rub",
            "rate_limit_per_minute",
            "max_concurrency",
            "ip_allowlist",
            "expires_at",
            "revoked_at",
            "last_used_at",
            "created_at",
            "active",
        )

    def get_active(self, obj):
        from .keys import key_is_active

        return key_is_active(obj)


class APIKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    scopes = serializers.ListField(
        child=serializers.ChoiceField(
            choices=["chat.completions", "models.read", "usage.read"]
        ),
        required=False,
    )
    allowed_models = serializers.ListField(
        child=serializers.SlugField(), required=False, default=list
    )
    allowed_endpoints = serializers.ListField(
        child=serializers.ChoiceField(choices=["chat.completions"]),
        required=False,
        default=list,
    )
    monthly_limit_rub = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True
    )
    rate_limit_per_minute = serializers.IntegerField(
        min_value=1, max_value=10000, required=False, default=60
    )
    max_concurrency = serializers.IntegerField(
        min_value=1, max_value=100, required=False, default=2
    )
    ip_allowlist = serializers.ListField(
        child=serializers.CharField(max_length=64), required=False, default=list
    )
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_ip_allowlist(self, value):
        try:
            for item in value:
                ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise serializers.ValidationError("Укажите IP-адрес или CIDR") from exc
        return value
