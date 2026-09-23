from rest_framework import serializers

from apps.projects.models import Project

from .models import Agent, AgentApproval, AgentRun, AgentStepRun, AgentTeam, AgentTeamMember


class AgentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Agent
        fields = [
            "id", "project", "name", "role", "objective", "instructions", "autonomy", "status",
            "system_level", "tool_policy", "graph", "memory_policy", "max_cost_rub_per_run",
            "max_cost_rub_per_day", "max_cost_rub_per_month", "max_steps", "max_tool_calls",
            "max_handoffs", "max_retries_per_step", "max_runtime_seconds", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_project(self, value):
        if value and value.owner_id != self.context["request"].user.id:
            raise serializers.ValidationError("Проект недоступен")
        return value

    def validate(self, attrs):
        instance = Agent(owner=self.context["request"].user)
        for field, value in attrs.items():
            setattr(instance, field, value)
        instance.clean()
        return attrs


class AgentTeamMemberSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source="agent.name", read_only=True)

    class Meta:
        model = AgentTeamMember
        fields = ["id", "agent", "agent_name", "role", "priority", "can_delegate", "enabled"]
        read_only_fields = ["id", "agent_name"]


class AgentTeamSerializer(serializers.ModelSerializer):
    members = AgentTeamMemberSerializer(many=True, read_only=True)
    director_name = serializers.CharField(source="director.name", read_only=True)

    class Meta:
        model = AgentTeam
        fields = [
            "id", "project", "name", "objective", "director", "director_name", "active",
            "max_cost_rub_per_run", "max_handoffs", "members", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "director_name", "members", "created_at", "updated_at"]

    def validate(self, attrs):
        user = self.context["request"].user
        director = attrs.get("director") or getattr(self.instance, "director", None)
        project = attrs.get("project") or getattr(self.instance, "project", None)
        if director and director.owner_id != user.id:
            raise serializers.ValidationError({"director": "Агент недоступен"})
        if project and project.owner_id != user.id:
            raise serializers.ValidationError({"project": "Проект недоступен"})
        return attrs


class AgentStepRunSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source="agent.name", read_only=True)

    class Meta:
        model = AgentStepRun
        fields = [
            "id", "agent", "agent_name", "sequence", "node_id", "title", "action_type", "state",
            "public_log", "cost_rub", "attempt", "started_at", "finished_at", "created_at",
        ]


class AgentApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentApproval
        fields = ["id", "step", "title", "description", "action_payload", "status", "decided_at", "created_at"]


class AgentRunSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source="agent.name", read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True)
    steps = AgentStepRunSerializer(many=True, read_only=True)
    approvals = AgentApprovalSerializer(many=True, read_only=True)

    class Meta:
        model = AgentRun
        fields = [
            "id", "agent", "agent_name", "team", "team_name", "project", "state", "objective",
            "plan", "cost_reserved_rub", "cost_actual_rub", "step_count", "tool_call_count",
            "handoff_count", "error_code", "error_message", "started_at", "finished_at", "created_at",
            "updated_at", "steps", "approvals",
        ]
        read_only_fields = fields
