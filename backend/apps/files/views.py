from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.projects.access import accessible_projects

from .models import FileAsset
from .serializers import FileAssetSerializer, FileChunkSerializer
from .services import process_file
from .validation import detect_and_validate


class FileAssetViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FileAssetSerializer

    def get_queryset(self):
        writable = self.action in {"destroy", "create"}
        projects = accessible_projects(self.request.user, write=writable)
        return (
            FileAsset.objects.filter(
                Q(owner=self.request.user) | Q(project__in=projects),
                deleted_at__isnull=True,
            )
            .select_related("project")
            .prefetch_related("jobs")
            .distinct()
        )

    def create(self, request, *_args, **_kwargs):
        key = request.headers.get("Idempotency-Key", "")
        if not key or len(key) > 160:
            raise ValidationError({"detail": "Корректный Idempotency-Key обязателен"})
        existing = FileAsset.objects.filter(owner=request.user, idempotency_key=key).first()
        if existing:
            return Response(self.get_serializer(existing).data)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": "Файл обязателен"})
        project = (
            accessible_projects(request.user, write=True)
            .filter(pk=request.data.get("project"), archived_at__isnull=True)
            .first()
        )
        if project is None:
            raise ValidationError({"project": "Проект не найден или недоступен"})
        try:
            original_name, detected, digest = detect_and_validate(uploaded)
        except DjangoValidationError as exc:
            raise ValidationError({"file": exc.messages}) from exc
        try:
            with transaction.atomic():
                asset = FileAsset.objects.create(
                    owner=request.user,
                    project=project,
                    blob="",
                    original_name=original_name,
                    declared_content_type=uploaded.content_type or "",
                    detected_type=detected,
                    size_bytes=uploaded.size,
                    sha256=digest,
                    status=FileAsset.Status.QUARANTINE,
                    scan_status=FileAsset.ScanStatus.BASIC_PASSED,
                    idempotency_key=key,
                )
        except IntegrityError:
            asset = FileAsset.objects.get(owner=request.user, idempotency_key=key)
            return Response(self.get_serializer(asset).data)
        asset.blob.save(original_name, uploaded, save=True)
        process_file(asset)
        return Response(self.get_serializer(asset).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def download(self, _request, pk=None):
        asset = self.get_object()
        if asset.status in {FileAsset.Status.DELETING, FileAsset.Status.DELETED}:
            raise ValidationError({"detail": "Файл удалён"})
        return FileResponse(asset.blob.open("rb"), as_attachment=True, filename=asset.original_name)

    @action(detail=True, methods=["get"])
    def chunks(self, _request, pk=None):
        asset = self.get_object()
        return Response(FileChunkSerializer(asset.chunks.all(), many=True).data)

    def destroy(self, request, *_args, **_kwargs):
        asset = self.get_object()
        asset.status = FileAsset.Status.DELETING
        asset.save(update_fields=["status", "updated_at"])
        asset.blob.delete(save=False)
        asset.chunks.all().delete()
        asset.status = FileAsset.Status.DELETED
        asset.deleted_at = timezone.now()
        asset.save(update_fields=["status", "deleted_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
