from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import MemoryCandidate, MemoryItem
from .serializers import (
    AcceptCandidateSerializer,
    MemoryCandidateSerializer,
    MemoryItemSerializer,
)
from .services import accept_candidate, reject_candidate


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


class MemoryCandidateViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MemoryCandidateSerializer

    def get_queryset(self):
        queryset = MemoryCandidate.objects.filter(owner=self.request.user).select_related(
            "project",
            "conversation",
            "source_message",
            "duplicate_of",
            "conflicts_with",
            "accepted_item",
        )
        requested_status = self.request.query_params.get("status")
        if requested_status and requested_status != "all":
            return queryset.filter(status=requested_status)
        if requested_status == "all":
            return queryset
        return queryset.filter(
            status__in=[MemoryCandidate.Status.PENDING, MemoryCandidate.Status.CONFLICT]
        )

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        candidate = self.get_object()
        serializer = AcceptCandidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            item = accept_candidate(
                candidate=candidate, user=request.user, **serializer.validated_data
            )
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        candidate.refresh_from_db()
        return Response(
            {
                "candidate": self.get_serializer(candidate).data,
                "memory": MemoryItemSerializer(item, context={"request": request}).data,
            }
        )

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        try:
            candidate = reject_candidate(candidate=self.get_object(), user=request.user)
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(self.get_serializer(candidate).data)

    @action(detail=False, methods=["post"], url_path="dismiss-all")
    def dismiss_all(self, _request):
        count = self.get_queryset().update(
            status=MemoryCandidate.Status.DISMISSED, reviewed_at=timezone.now()
        )
        return Response({"dismissed": count}, status=status.HTTP_200_OK)
