from rest_framework import serializers

from apps.projects.access import accessible_projects

from .models import MemoryCandidate, MemoryItem, MemoryRevision
from .services import create_revision, normalize_content, subject_key_for_content


class MemoryRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MemoryRevision
        fields = ("id", "content", "scope", "created_at")


class MemoryItemSerializer(serializers.ModelSerializer):
    revisions = MemoryRevisionSerializer(many=True, read_only=True)

    class Meta:
        model = MemoryItem
        fields = (
            "id",
            "project",
            "conversation",
            "scope",
            "memory_type",
            "content",
            "importance_score",
            "confidence_score",
            "trust_level",
            "source_kind",
            "source_message",
            "valid_from",
            "valid_until",
            "status",
            "pinned",
            "enabled",
            "created_at",
            "updated_at",
            "revisions",
        )
        read_only_fields = (
            "id",
            "confidence_score",
            "trust_level",
            "source_kind",
            "source_message",
            "created_at",
            "updated_at",
            "revisions",
        )

    def validate(self, attrs):
        instance = self.instance
        scope = attrs.get("scope", getattr(instance, "scope", None))
        project = attrs.get("project", getattr(instance, "project", None))
        conversation = attrs.get("conversation", getattr(instance, "conversation", None))
        user = self.context["request"].user
        if scope == MemoryItem.Scope.GLOBAL:
            project = conversation = None
        elif scope == MemoryItem.Scope.PROJECT:
            conversation = None
            if (
                project is None
                or not accessible_projects(user, write=True).filter(pk=project.pk).exists()
            ):
                raise serializers.ValidationError({"project": "Проект не найден или недоступен"})
        elif scope == MemoryItem.Scope.CONVERSATION:
            project = None
            if conversation is None or conversation.owner_id != user.id:
                raise serializers.ValidationError({"conversation": "Чат не найден или недоступен"})
        else:
            raise serializers.ValidationError({"scope": "Неизвестная область памяти"})
        attrs["project"] = project
        attrs["conversation"] = conversation
        return attrs

    def create(self, validated_data):
        item = MemoryItem.objects.create(
            owner=self.context["request"].user,
            normalized_content=normalize_content(validated_data["content"]),
            subject_key=subject_key_for_content(validated_data["content"]),
            source_kind="user_manual",
            **validated_data,
        )
        create_revision(item, self.context["request"].user)
        return item

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.normalized_content = normalize_content(instance.content)
        instance.subject_key = subject_key_for_content(instance.content)
        instance.full_clean()
        instance.save()
        create_revision(instance, self.context["request"].user)
        return instance


class MemoryCandidateSerializer(serializers.ModelSerializer):
    duplicate_content = serializers.CharField(source="duplicate_of.content", read_only=True)
    conflict_content = serializers.CharField(source="conflicts_with.content", read_only=True)

    class Meta:
        model = MemoryCandidate
        fields = (
            "id",
            "project",
            "conversation",
            "source_message",
            "suggested_scope",
            "memory_type",
            "content",
            "subject_key",
            "confidence_score",
            "trust_level",
            "source_kind",
            "extraction_version",
            "reason",
            "status",
            "duplicate_content",
            "conflict_content",
            "accepted_item",
            "created_at",
            "reviewed_at",
        )
        read_only_fields = fields


class AcceptCandidateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=1000, required=False)
    scope = serializers.ChoiceField(choices=MemoryItem.Scope.choices, required=False)
