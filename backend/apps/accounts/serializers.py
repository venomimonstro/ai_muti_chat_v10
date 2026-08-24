from django.contrib.auth import authenticate
from rest_framework import serializers

from .models import Notification, SupportRequest, User, UserPreference


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "role", "status")
        read_only_fields = ("id", "role", "status")


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ("username", "email", "password")

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(**attrs)
        if not user or user.status != User.Status.ACTIVE:
            raise serializers.ValidationError("Неверные данные или аккаунт недоступен")
        return {"user": user}


class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = (
            "low_balance_threshold_rub",
            "daily_spend_limit_rub",
            "monthly_spend_limit_rub",
            "product_notifications",
            "billing_notifications",
            "compact_sidebar",
            "updated_at",
        )
        read_only_fields = ("updated_at",)

    def validate(self, attrs):
        for field in ("daily_spend_limit_rub", "monthly_spend_limit_rub"):
            value = attrs.get(field)
            if value is not None and value <= 0:
                raise serializers.ValidationError({field: "Лимит должен быть больше нуля"})
        threshold = attrs.get("low_balance_threshold_rub")
        if threshold is not None and threshold < 0:
            raise serializers.ValidationError(
                {"low_balance_threshold_rub": "Порог не может быть отрицательным"}
            )
        return attrs


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "title", "body", "level", "action_url", "read_at", "created_at")
        read_only_fields = fields


class SupportRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportRequest
        fields = ("id", "subject", "message", "status", "created_at", "updated_at")
        read_only_fields = ("id", "status", "created_at", "updated_at")


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Текущий пароль указан неверно")
        return value
