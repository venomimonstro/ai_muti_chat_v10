from datetime import timedelta

from django.conf import settings
from rest_framework import serializers

from .models import Agent, AgentApproval, AgentRun, AgentStepRun, AgentTeam, AgentTeamMember, AgentVersion
from .tool_policy import validate_tool_policy


def _ancestor_node_ids(edges, node_id):
    reverse = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("from") or "").strip()
        target = str(edge.get("to") or "").strip()
        if source and target:
            reverse.setdefault(target, set()).add(source)
    ancestors = set()
    stack = list(reverse.get(str(node_id), set()))
    while stack:
        current = stack.pop()
        if current in ancestors:
            continue
        ancestors.add(current)
        stack.extend(reverse.get(current, set()))
    return ancestors


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

    def validate_tool_policy(self, value):
        return validate_tool_policy(value)

    def validate(self, attrs):
        instance = self.instance or Agent(owner=self.context["request"].user)
        for field, value in attrs.items():
            setattr(instance, field, value)
        instance.clean()

        policy = str((instance.tool_policy or {}).get("publish") or "disabled").strip().lower()
        if policy == "auto" and instance.autonomy != Agent.Autonomy.AUTONOMOUS:
            raise serializers.ValidationError(
                {"tool_policy": "Автопубликация доступна только полностью автономному агенту"}
            )

        graph = instance.graph if isinstance(instance.graph, dict) else {}
        nodes = list(graph.get("nodes") or [])
        edges = list(graph.get("edges") or [])
        node_types = {
            str((node or {}).get("id") or "").strip(): str((node or {}).get("type") or "llm").strip().lower()
            for node in nodes
            if isinstance(node, dict)
        }
        publish_nodes = [node_id for node_id, node_type in node_types.items() if node_type == "publish"]
        approval_nodes = {node_id for node_id, node_type in node_types.items() if node_type == "approval"}
        if publish_nodes and policy == "disabled":
            raise serializers.ValidationError(
                {"tool_policy": "В карте есть публикация, но инструмент publish отключён"}
            )
        if policy == "approval":
            for publish_node in publish_nodes:
                if not (_ancestor_node_ids(edges, publish_node) & approval_nodes):
                    raise serializers.ValidationError(
                        {"graph": f"Перед публикацией «{publish_node}» добавьте блок подтверждения в той же цепочке workflow"}
                    )
        return attrs


class AgentVersionSerializer(serializers.ModelSerializer):
    created_by = serializers.CharField(source="created_by.email", read_only=True)

    class Meta:
        model = AgentVersion
        fields = ["id", "version", "snapshot", "created_by", "created_at"]
        read_only_fields = fields


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
            "id", "project", "name", "objective", "kind", "director", "director_name", "active",
            "max_cost_rub_per_run", "max_handoffs", "members", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "director_name", "members", "created_at", "updated_at"]

    def validate(self, attrs):
        user = self.context["request"].user
        current_director = getattr(self.instance, "director", None)
        director = attrs.get("director") or current_director
        project = attrs.get("project") or getattr(self.instance, "project", None)
        kind = attrs.get("kind") or getattr(self.instance, "kind", AgentTeam.Kind.GENERIC)
        if director and director.owner_id != user.id:
            raise serializers.ValidationError({"director": "Агент недоступен"})
        if project and project.owner_id != user.id:
            raise serializers.ValidationError({"project": "Проект недоступен"})
        if kind == AgentTeam.Kind.DEVELOPMENT and not project:
            raise serializers.ValidationError({"project": "Команда разработки должна быть привязана к проекту"})
        if self.instance and "director" in attrs and director and director.id != self.instance.director_id:
            if not AgentTeamMember.objects.filter(team=self.instance, agent=director, enabled=True).exists():
                raise serializers.ValidationError(
                    {"director": "Новый руководитель должен быть активным участником команды"}
                )
        return attrs


class AgentStepRunSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source="agent.name", read_only=True)

    class Meta:
        model = AgentStepRun
        fields = [
            "id", "agent", "agent_name", "sequence", "node_id", "title", "action_type", "state",
            "public_log", "output_payload", "cost_rub", "attempt", "started_at", "finished_at", "created_at",
        ]
        read_only_fields = fields


class AgentApprovalSerializer(serializers.ModelSerializer):
    expires_at = serializers.SerializerMethodField()

    def get_expires_at(self, obj):
        hours = max(1, int(getattr(settings, "AGENT_APPROVAL_TIMEOUT_HOURS", 72)))
        return obj.created_at + timedelta(hours=hours)

    class Meta:
        model = AgentApproval
        fields = [
            "id", "step", "title", "description", "action_payload", "status",
            "decided_at", "created_at", "expires_at",
        ]
        read_only_fields = fields


class AgentRunSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source="agent.name", read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True)
    team_kind = serializers.CharField(source="team.kind", read_only=True)
    steps = AgentStepRunSerializer(many=True, read_only=True)
    approvals = AgentApprovalSerializer(many=True, read_only=True)

    class Meta:
        model = AgentRun
        fields = [
            "id", "agent", "agent_name", "team", "team_name", "team_kind", "project", "state", "objective",
            "input_payload", "output_payload", "plan", "cost_reserved_rub", "cost_actual_rub",
            "step_count", "tool_call_count", "handoff_count", "error_code", "error_message",
            "started_at", "finished_at", "created_at", "updated_at", "steps", "approvals",
        ]
        read_only_fields = fields
