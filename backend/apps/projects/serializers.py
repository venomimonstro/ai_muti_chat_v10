from django.db import transaction
from rest_framework import serializers

from .models import Project, ProjectInstruction, ProjectMembership


class ProjectSerializer(serializers.ModelSerializer):
    instruction = serializers.CharField(write_only=True, required=False, allow_blank=True)
    active_instruction = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = (
            "id",
            "name",
            "description",
            "instruction",
            "active_instruction",
            "role",
            "archived_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "archived_at", "created_at", "updated_at")

    def get_active_instruction(self, obj):
        prefetched = getattr(obj, "_prefetched_objects_cache", {}).get("instructions")
        if prefetched is not None:
            for item in prefetched:
                if item.active:
                    return item.content
            return ""
        item = obj.instructions.filter(active=True).first()
        return item.content if item else ""

    def get_role(self, obj):
        user = self.context["request"].user
        if obj.owner_id == user.id:
            return ProjectMembership.Role.OWNER
        memberships = getattr(obj, "_prefetched_objects_cache", {}).get("memberships")
        if memberships is not None:
            for membership in memberships:
                if membership.user_id == user.id:
                    return membership.role
            return None
        membership = obj.memberships.filter(user=user).first()
        return membership.role if membership else None

    @transaction.atomic
    def create(self, validated_data):
        instruction = validated_data.pop("instruction", "")
        user = self.context["request"].user
        project = Project.objects.create(owner=user, **validated_data)
        ProjectMembership.objects.create(
            project=project, user=user, role=ProjectMembership.Role.OWNER
        )
        if instruction:
            ProjectInstruction.objects.create(
                project=project, content=instruction, version=1, created_by=user
            )
        return project

    @transaction.atomic
    def update(self, instance, validated_data):
        instance = Project.objects.select_for_update().get(pk=instance.pk)
        instruction = validated_data.pop("instruction", None)
        instance = super().update(instance, validated_data)
        if instruction is not None:
            current = instance.instructions.select_for_update().filter(active=True).first()
            if current is None or current.content != instruction:
                instance.instructions.filter(active=True).update(active=False)
                version = (
                    instance.instructions.order_by("-version")
                    .values_list("version", flat=True)
                    .first()
                    or 0
                ) + 1
                ProjectInstruction.objects.create(
                    project=instance,
                    content=instruction,
                    version=version,
                    created_by=self.context["request"].user,
                )
        return instance
