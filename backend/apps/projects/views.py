from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .access import accessible_projects
from .serializers import ProjectSerializer


class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer

    def get_queryset(self):
        write = self.action in {"update", "partial_update", "destroy", "restore"}
        return accessible_projects(self.request.user, write=write).prefetch_related(
            "instructions", "memberships"
        )

    def perform_destroy(self, instance):
        instance.archived_at = timezone.now()
        instance.save(update_fields=["archived_at", "updated_at"])

    @action(detail=True, methods=["post"])
    def restore(self, _request, pk=None):
        project = self.get_object()
        project.archived_at = None
        project.save(update_fields=["archived_at", "updated_at"])
        return Response(self.get_serializer(project).data)
