from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers, viewsets

from .models import Agent, AgentTeam
from .schedule_models import AgentSchedule


class AgentScheduleSerializer(serializers.ModelSerializer):
    subject_name = serializers.SerializerMethodField()
    subject_type = serializers.SerializerMethodField()

    class Meta:
        model = AgentSchedule
        fields = [
            "id", "agent", "team", "subject_name", "subject_type", "name", "objective", "enabled",
            "interval_minutes", "next_run_at", "last_run_at", "last_run", "skip_if_running",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "subject_name", "subject_type", "last_run_at", "last_run", "created_at", "updated_at"]

    def get_subject_name(self, obj):
        subject = obj.agent or obj.team
        return subject.name if subject else ""

    def get_subject_type(self, obj):
        return "agent" if obj.agent_id else "team"

    def validate(self, attrs):
        user = self.context["request"].user
        agent = attrs.get("agent") if "agent" in attrs else getattr(self.instance, "agent", None)
        team = attrs.get("team") if "team" in attrs else getattr(self.instance, "team", None)
        if bool(agent) == bool(team):
            raise serializers.ValidationError("Выберите либо одного агента, либо одну команду")
        if agent and agent.owner_id != user.id:
            raise serializers.ValidationError({"agent": "Агент недоступен"})
        if team and team.owner_id != user.id:
            raise serializers.ValidationError({"team": "Команда недоступна"})
        interval = int(attrs.get("interval_minutes", getattr(self.instance, "interval_minutes", 1440)))
        if interval < 5:
            raise serializers.ValidationError({"interval_minutes": "Минимальный интервал — 5 минут"})
        return attrs

    def create(self, validated_data):
        if not validated_data.get("next_run_at"):
            validated_data["next_run_at"] = timezone.now() + timedelta(minutes=int(validated_data.get("interval_minutes", 1440)))
        return AgentSchedule.objects.create(owner=self.context["request"].user, **validated_data)


class AgentScheduleViewSet(viewsets.ModelViewSet):
    serializer_class = AgentScheduleSerializer

    def get_queryset(self):
        return (
            AgentSchedule.objects.filter(owner=self.request.user)
            .select_related("agent", "team", "last_run")
            .order_by("next_run_at", "name")
        )

    def perform_destroy(self, instance):
        instance.delete()
