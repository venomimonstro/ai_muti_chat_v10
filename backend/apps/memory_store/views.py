from django.db.models import Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import MemoryItem
from .serializers import MemoryItemSerializer


class MemoryItemViewSet(viewsets.ModelViewSet):
    serializer_class = MemoryItemSerializer

    def get_queryset(self):
        queryset = (
            MemoryItem.objects.filter(owner=self.request.user)
            .exclude(status=MemoryItem.Status.DELETED)
            .select_related("project", "conversation", "source_message")
            .prefetch_related("revisions")
        )
        query = self.request.query_params.get("q", "").strip()
        if query:
            queryset = queryset.filter(
                Q(content__icontains=query) | Q(normalized_content__icontains=query.lower())
            )
        for field in ("scope", "status", "project", "conversation"):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset

    def perform_destroy(self, instance):
        instance.status = MemoryItem.Status.DELETED
        instance.enabled = False
        instance.save(update_fields=["status", "enabled", "updated_at"])

    @action(detail=True, methods=["post"])
    def archive(self, _request, pk=None):
        item = self.get_object()
        item.status = (
            MemoryItem.Status.ACTIVE
            if item.status == MemoryItem.Status.ARCHIVED
            else MemoryItem.Status.ARCHIVED
        )
        item.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=["post"])
    def pin(self, _request, pk=None):
        item = self.get_object()
        item.pinned = not item.pinned
        item.save(update_fields=["pinned", "updated_at"])
        return Response(self.get_serializer(item).data)
