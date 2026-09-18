import os

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from apps.projects.access import accessible_projects

from .models import FileAsset
from .rag import retrieve_project_chunks
from .serializers import FileAssetSerializer, FileChunkSerializer
from .services import process_file
from .tasks import process_file_task
from .validation import detect_and_validate


class FileStorageUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Хранилище файлов временно недоступно"
    default_code = "file_storage_unavailable"


class FileProcessingUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Обработка файлов временно недоступна. Попробуйте позже."
    default_code = "file_processing_unavailable"


def _validate_idempotent_replay(asset, project, original_name, uploaded, digest):
    if (
        asset.project_id != project.id
        or asset.original_name != original_name
        or asset.size_bytes != uploaded.size
        or asset.sha256 != digest
    ):
        raise ValidationError(
            {"Idempotency-Key": "Ключ уже использован для другой загрузки"}
        )


def _storage_limits():
    return {
        "user_bytes": int(os.getenv("FILE_USER_STORAGE_LIMIT_BYTES", str(512 * 1024 * 1024))),
        "project_bytes": int(os.getenv("FILE_PROJECT_STORAGE_LIMIT_BYTES", str(256 * 1024 * 1024))),
        "user_files": int(os.getenv("FILE_USER_MAX_FILES", "1000")),
        "project_files": int(os.getenv("FILE_PROJECT_MAX_FILES", "500")),
    }


def _enforce_storage_quota(*, user, project, incoming_bytes):
    limits = _storage_limits()
    active = FileAsset.objects.filter(deleted_at__isnull=True).exclude(
        status__in=[FileAsset.Status.DELETING, FileAsset.Status.DELETED]
    )
    user_usage = active.filter(owner=user).aggregate(
        bytes=Sum("size_bytes"), files=Count("id")
    )
    project_usage = active.filter(project=project).aggregate(
        bytes=Sum("size_bytes"), files=Count("id")
    )
    user_bytes = int(user_usage["bytes"] or 0)
    project_bytes = int(project_usage["bytes"] or 0)
    user_files = int(user_usage["files"] or 0)
    project_files = int(project_usage["files"] or 0)
    if user_files + 1 > limits["user_files"]:
        raise ValidationError({"file": "Достигнут лимит количества файлов аккаунта"})
    if project_files + 1 > limits["project_files"]:
        raise ValidationError({"file": "Достигнут лимит количества файлов проекта"})
    if user_bytes + incoming_bytes > limits["user_bytes"]:
        raise ValidationError({"file": "Достигнут лимит хранилища аккаунта"})
    if project_bytes + incoming_bytes > limits["project_bytes"]:
        raise ValidationError({"file": "Достигнут лимит хранилища проекта"})


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
        existing = FileAsset.objects.filter(owner=request.user, idempotency_key=key).first()
        if existing:
            _validate_idempotent_replay(existing, project, original_name, uploaded, digest)
            return Response(self.get_serializer(existing).data)
        try:
            with transaction.atomic():
                request.user.__class__.objects.select_for_update().only("pk").get(pk=request.user.pk)
                project = project.__class__.objects.select_for_update().get(pk=project.pk)
                raced = FileAsset.objects.filter(owner=request.user, idempotency_key=key).first()
                if raced:
                    _validate_idempotent_replay(raced, project, original_name, uploaded, digest)
                    return Response(self.get_serializer(raced).data)
                _enforce_storage_quota(
                    user=request.user,
                    project=project,
                    incoming_bytes=uploaded.size,
                )
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
            _validate_idempotent_replay(asset, project, original_name, uploaded, digest)
            return Response(self.get_serializer(asset).data)
        try:
            asset.blob.save(original_name, uploaded, save=True)
        except Exception as exc:
            try:
                asset.blob.delete(save=False)
            finally:
                asset.delete()
            raise FileStorageUnavailable() from exc

        async_processing = os.getenv(
            "FILE_PROCESSING_ASYNC",
            "false" if settings.DEBUG else "true",
        ).lower() == "true"
        if async_processing:
            try:
                process_file_task.delay(str(asset.id))
            except Exception as exc:
                try:
                    asset.blob.delete(save=False)
                finally:
                    asset.delete()
                raise FileProcessingUnavailable() from exc
            asset.status = FileAsset.Status.UPLOADED
            asset.save(update_fields=["status", "updated_at"])
        else:
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

    @action(detail=False, methods=["post"], url_path="retrieve")
    def retrieve_chunks(self, request):
        project_id = request.data.get("project")
        query = str(request.data.get("query", "")).strip()
        if not project_id or not query:
            raise ValidationError({"detail": "Поля project и query обязательны"})
        if len(query) > 10_000:
            raise ValidationError({"query": "Запрос не должен превышать 10000 символов"})
        try:
            limit = int(request.data.get("limit", settings.SMART_CONTEXT_FILE_CHUNK_LIMIT))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"limit": "Ожидается целое число"}) from exc
        if not accessible_projects(request.user).filter(pk=project_id).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        hits = retrieve_project_chunks(
            user=request.user, project_id=project_id, query=query, limit=limit
        )
        return Response(
            {
                "results": [
                    {
                        "score": round(hit.score, 4),
                        "lexical_score": round(hit.lexical_score, 4),
                        "vector_score": round(hit.vector_score, 4),
                        "content": hit.chunk.content,
                        "citation": hit.citation,
                    }
                    for hit in hits
                ]
            }
        )

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
