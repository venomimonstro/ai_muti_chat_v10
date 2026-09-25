from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
            "cadence", "interval_minutes", "local_time", "timezone_name", "weekdays",
            "next_run_at", "last_run_at", "last_run", "skip_if_running",
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

        cadence = attrs.get("cadence", getattr(self.instance, "cadence", AgentSchedule.Cadence.INTERVAL))
        interval = int(attrs.get("interval_minutes", getattr(self.instance, "interval_minutes", 1440)))
        local_time = attrs.get("local_time", getattr(self.instance, "local_time", None))
        timezone_name = str(attrs.get("timezone_name", getattr(self.instance, "timezone_name", "Europe/Moscow")))
        weekdays = list(attrs.get("weekdays", getattr(self.instance, "weekdays", [])) or [])

        if interval < 5:
            raise serializers.ValidationError({"interval_minutes": "Минимальный интервал — 5 минут"})
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            raise serializers.ValidationError({"timezone_name": "Неизвестный часовой пояс"})
        if cadence != AgentSchedule.Cadence.INTERVAL and local_time is None:
            raise serializers.ValidationError({"local_time": "Укажите время запуска"})
        if any(not isinstance(day, int) or day < 0 or day > 6 for day in weekdays):
            raise serializers.ValidationError({"weekdays": "Используйте дни недели от 0 до 6"})
        if cadence == AgentSchedule.Cadence.WEEKLY and not weekdays:
            raise serializers.ValidationError({"weekdays": "Выберите хотя бы один день недели"})
        return attrs

    def _set_next_run(self, instance):
        instance.next_run_at = instance.compute_next_run(after=timezone.now())
        instance.save(update_fields=["next_run_at", "updated_at"])
        return instance

    def create(self, validated_data):
        # next_run_at is derived by the server. A client cannot schedule a hidden
        # arbitrary timestamp that disagrees with the visible cadence settings.
        validated_data.pop("next_run_at", None)
        placeholder = timezone.now() + timedelta(minutes=max(5, int(validated_data.get("interval_minutes", 1440))))
        instance = AgentSchedule.objects.create(
            owner=self.context["request"].user,
            next_run_at=placeholder,
            **validated_data,
        )
        return self._set_next_run(instance)

    def update(self, instance, validated_data):
        validated_data.pop("next_run_at", None)
        scheduling_fields = {"cadence", "interval_minutes", "local_time", "timezone_name", "weekdays"}
        recompute = bool(scheduling_fields.intersection(validated_data.keys()))
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.full_clean()
        instance.save()
        if recompute:
            instance.next_run_at = instance.compute_next_run(after=timezone.now())
            instance.save(update_fields=["next_run_at", "updated_at"])
        return instance


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
