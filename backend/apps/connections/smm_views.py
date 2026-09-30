from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from .models import ExternalConnection
from .smm_media import prepare_item_media, search_free_stock
from .smm_models import SMMContentItem, SMMContentPlan, SMMPublicationAttempt
from .smm_serializers import SMMContentItemSerializer, SMMContentPlanSerializer, SMMPublicationAttemptSerializer
from .smm_service import ensure_smm_agent, publish_item, start_plan_generation, sync_generated_plan


class SMMContentPlanViewSet(viewsets.ModelViewSet):
    serializer_class = SMMContentPlanSerializer

    def get_queryset(self):
        return (
            SMMContentPlan.objects.filter(owner=self.request.user)
            .select_related("agent", "connection", "project", "generation_run")
            .prefetch_related("items__publication_attempts")
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.generation_run_id:
            try:
                sync_generated_plan(instance)
            except DjangoValidationError:
                pass
            instance = self.get_queryset().get(pk=instance.pk)
        return Response(self.get_serializer(instance).data)

    @action(detail=False, methods=["post"], url_path="bootstrap-agent")
    def bootstrap_agent(self, request):
        connection_id = str(request.data.get("connection") or "").strip()
        connection = ExternalConnection.objects.filter(
            pk=connection_id,
            owner=request.user,
            kind=ExternalConnection.Kind.VK,
            enabled=True,
            health_state=ExternalConnection.Health.HEALTHY,
        ).first()
        if connection is None:
            raise ValidationError({"connection": "Сначала подключите и проверьте ВКонтакте"})
        if not str((connection.metadata or {}).get("selected_group_id") or "").strip():
            raise ValidationError({"connection": "Сначала выберите сообщество VK"})
        try:
            agent = ensure_smm_agent(owner=request.user, connection=connection)
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc
        return Response({"agent": str(agent.id), "name": agent.name, "status": agent.status})

    @action(detail=True, methods=["post"], url_path="generate")
    def generate(self, request, pk=None):
        plan = self.get_object()
        count = request.data.get("post_count", 12)
        try:
            count = max(1, min(int(count), 60))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"post_count": "Укажите число от 1 до 60"}) from exc
        try:
            run = start_plan_generation(plan, post_count=count)
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc
        return Response(
            {"run": str(run.id), "state": run.state, "plan": str(plan.id)},
            status=status.HTTP_202_ACCEPTED if run.state != "completed" else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="sync-generation")
    def sync_generation(self, request, pk=None):
        plan = self.get_object()
        try:
            result = sync_generated_plan(plan)
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc
        plan = self.get_queryset().get(pk=plan.pk)
        return Response({**result, "plan": self.get_serializer(plan).data})


class SMMContentItemViewSet(viewsets.ModelViewSet):
    serializer_class = SMMContentItemSerializer

    def get_queryset(self):
        queryset = (
            SMMContentItem.objects.filter(plan__owner=self.request.user)
            .select_related("plan", "plan__connection", "plan__agent", "plan__owner")
            .prefetch_related("publication_attempts")
        )
        plan_id = str(self.request.query_params.get("plan") or "").strip()
        if plan_id:
            queryset = queryset.filter(plan_id=plan_id)
        return queryset

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.status == SMMContentItem.Status.PUBLISHED:
            immutable = {"content", "scheduled_at", "status", "vk_attachment"}
            if immutable.intersection(serializer.validated_data):
                raise ValidationError({"detail": "Опубликованный пост нельзя изменять. Создайте новый пост."})
        item = serializer.save()
        try:
            item.full_clean()
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc

    @action(detail=False, methods=["get"], url_path="stock-search")
    def stock_search(self, request):
        query = str(request.query_params.get("q") or "").strip()
        try:
            return Response(search_free_stock(query, per_page=12))
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc

    @action(detail=True, methods=["post"], url_path="prepare-media")
    def prepare_media(self, request, pk=None):
        item = self.get_object()
        if item.status == SMMContentItem.Status.PUBLISHED:
            raise ValidationError({"detail": "Пост уже опубликован"})
        try:
            attachment = prepare_item_media(item)
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc
        item.vk_attachment = attachment
        item.publish_error = ""
        item.save(update_fields=["vk_attachment", "publish_error", "updated_at"])
        return Response(self.get_serializer(item).data)

    @action(detail=True, methods=["post"], url_path="publish")
    def publish(self, request, pk=None):
        item = self.get_object()
        key = str(request.headers.get("Idempotency-Key") or request.data.get("idempotency_key") or "").strip()
        if not key:
            raise ValidationError({"detail": "Idempotency-Key обязателен"})
        try:
            attempt = publish_item(item, idempotency_key=key)
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc
        item = self.get_queryset().get(pk=item.pk)
        return Response(
            {
                "item": self.get_serializer(item).data,
                "attempt": SMMPublicationAttemptSerializer(attempt).data,
            }
        )

    @action(detail=True, methods=["post"], url_path="schedule")
    @transaction.atomic
    def schedule(self, request, pk=None):
        item = self.get_object()
        if item.status == SMMContentItem.Status.PUBLISHED:
            raise ValidationError({"detail": "Пост уже опубликован"})
        scheduled_at = request.data.get("scheduled_at") or item.scheduled_at
        serializer = self.get_serializer(
            item,
            data={"scheduled_at": scheduled_at, "status": SMMContentItem.Status.SCHEDULED},
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        try:
            updated.full_clean()
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc
        return Response(self.get_serializer(updated).data)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        item = self.get_object()
        if item.status == SMMContentItem.Status.PUBLISHED:
            raise ValidationError({"detail": "Пост уже опубликован"})
        item.status = SMMContentItem.Status.APPROVED
        item.publish_error = ""
        item.save(update_fields=["status", "publish_error", "updated_at"])
        return Response(self.get_serializer(item).data)


class SMMPublicationAttemptViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SMMPublicationAttemptSerializer

    def get_queryset(self):
        queryset = SMMPublicationAttempt.objects.filter(item__plan__owner=self.request.user).select_related("item")
        item_id = str(self.request.query_params.get("item") or "").strip()
        if item_id:
            queryset = queryset.filter(item_id=item_id)
        return queryset
