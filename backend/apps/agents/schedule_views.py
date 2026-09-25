from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Agent, AgentRun, AgentTeam
from .run_views import create_single_agent_run
from .schedule_models import AgentSchedule
from .serializers import AgentRunSerializer


ACTIVE_RUN_STATES = {
    AgentRun.State.QUEUED,
    AgentRun.State.PLANNING,
    AgentRun.State.RUNNING,
    AgentRun.State.WAITING_TOOL,
    AgentRun.State.WAITING_APPROVAL,
    AgentRun.State.REVIEWING,
}


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
        was_enabled = bool(instance.enabled)
        will_enable = bool(validated_data.get("enabled", instance.enabled))
        recompute = bool(scheduling_fields.intersection(validated_data.keys())) or (not was_enabled and will_enable)
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

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        schedule = self.get_object()
        limit_raw = str(request.query_params.get("limit") or "10").strip()
        try:
            limit = max(1, min(int(limit_raw), 50))
        except ValueError:
            raise serializers.ValidationError({"limit": "Используйте число от 1 до 50"})
        runs = (
            AgentRun.objects.filter(
                owner=request.user,
                input_payload__schedule_id=str(schedule.id),
            )
            .select_related("agent", "team", "project")
            .prefetch_related("steps__agent", "approvals")
            .order_by("-created_at")[:limit]
        )
        return Response(AgentRunSerializer(runs, many=True).data)

    @action(detail=True, methods=["post"], url_path="run-now")
    @transaction.atomic
    def run_now(self, request, pk=None):
        scoped = self.get_object()
        schedule = (
            AgentSchedule.objects.select_for_update()
            .select_related("agent", "team")
            .get(pk=scoped.pk, owner=request.user)
        )

        if schedule.agent_id:
            subject = Agent.objects.select_for_update().get(pk=schedule.agent_id, owner=request.user)
            if subject.status != Agent.Status.ACTIVE:
                raise serializers.ValidationError({"detail": "Сотрудник приостановлен"})
            active = AgentRun.objects.filter(owner=request.user, agent=subject, state__in=ACTIVE_RUN_STATES).first()
            if active is not None:
                return Response(AgentRunSerializer(active).data, status=status.HTTP_200_OK)
            objective = (schedule.objective or subject.objective or "").strip()
            if not objective:
                raise serializers.ValidationError({"objective": "У расписания и сотрудника нет задачи"})
            run = create_single_agent_run(
                owner=request.user,
                agent=subject,
                objective=objective,
                input_payload={"trigger": "schedule_run_now", "schedule_id": str(schedule.id)},
            )
        else:
            subject = AgentTeam.objects.select_for_update().get(pk=schedule.team_id, owner=request.user)
            if not subject.active:
                raise serializers.ValidationError({"detail": "Команда приостановлена"})
            active = AgentRun.objects.filter(owner=request.user, team=subject, state__in=ACTIVE_RUN_STATES).first()
            if active is not None:
                return Response(AgentRunSerializer(active).data, status=status.HTTP_200_OK)
            objective = (schedule.objective or subject.objective or "").strip()
            if not objective:
                raise serializers.ValidationError({"objective": "У расписания и команды нет задачи"})
            run = AgentRun.objects.create(
                owner=request.user,
                team=subject,
                project=subject.project,
                objective=objective,
                input_payload={"trigger": "schedule_run_now", "schedule_id": str(schedule.id)},
                state=AgentRun.State.QUEUED,
            )
            from .tasks import enqueue_agent_run

            transaction.on_commit(lambda run_id=str(run.id): enqueue_agent_run(run_id))

        schedule.last_run_at = timezone.now()
        schedule.last_run = run
        schedule.save(update_fields=["last_run_at", "last_run", "updated_at"])
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        instance.delete()
