from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, OuterRef, Prefetch, Subquery
from django.http import StreamingHttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response

from apps.ai_registry.models import AIModel

from .branches import ensure_active_branch, fork_branch, visible_messages
from .compare import (
    branch_from_variant,
    compare_preview,
    run_compare,
    serialize_compare,
    synthesize_compare,
)
from .models import CompareVariant, Conversation, ConversationDraft, Message
from .serializers import (
    ConversationDraftSerializer,
    ConversationSerializer,
    ConversationSummarySerializer,
    SendMessageSerializer,
)
from .services import generate_reply
from .streaming import prepare, run


class ConversationViewSet(viewsets.ModelViewSet):
    serializer_class = ConversationSerializer

    def get_serializer_class(self):
        if self.action == "list":
            return ConversationSummarySerializer
        return ConversationSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action in {
            "retrieve",
            "update",
            "partial_update",
            "activate_branch",
            "compare_branch",
        }:
            context["include_message_context"] = True
        if self.action == "branches" and self.request.method == "POST":
            context["include_message_context"] = True
        return context

    def get_queryset(self):
        base = Conversation.objects.filter(owner=self.request.user).select_related("active_branch")
        if self.action == "list":
            last_message = Message.objects.filter(conversation=OuterRef("pk")).order_by("-created_at")
            return (
                base.prefetch_related("branches")
                .annotate(
                    message_count=Count("messages", distinct=True),
                    last_message_preview=Subquery(last_message.values("content")[:1]),
                )
                .order_by("-updated_at")
            )
        return base.prefetch_related(
            "branches",
            Prefetch(
                "messages", queryset=Message.objects.select_related("generation_response")
            ),
        ).order_by("-updated_at")

    def perform_create(self, serializer):
        conversation = serializer.save(owner=self.request.user)
        ensure_active_branch(conversation, self.request.user)

    @action(detail=True, methods=["get", "post"])
    def branches(self, request, pk=None):
        conversation = self.get_object()
        if request.method == "POST":
            source = (
                visible_messages(conversation).filter(pk=request.data.get("source_message")).first()
            )
            if source is None:
                raise APIValidationError({"source_message": "Сообщение не найдено"})
            try:
                fork_branch(
                    conversation=conversation,
                    user=request.user,
                    source_message=source,
                    title=request.data.get("title") or "Альтернативная ветка",
                )
            except ValueError as exc:
                raise APIValidationError({"source_message": str(exc)}) from exc
        conversation.refresh_from_db()
        return Response(self.get_serializer(conversation).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"branches/(?P<branch_id>[^/.]+)/activate",
    )
    def activate_branch(self, request, pk=None, branch_id=None):
        conversation = self.get_object()
        branch = conversation.branches.filter(pk=branch_id).first()
        if branch is None:
            raise APIValidationError({"branch": "Ветка не найдена"})
        conversation.active_branch = branch
        conversation.save(update_fields=["active_branch", "updated_at"])
        conversation.refresh_from_db()
        return Response(self.get_serializer(conversation).data)

    def _compare_payload(self, request, conversation):
        prompt = str(request.data.get("prompt", "")).strip()
        model_slugs = request.data.get("models") or []
        if not prompt:
            raise APIValidationError({"prompt": "Запрос обязателен"})
        if not isinstance(model_slugs, list):
            raise APIValidationError({"models": "Ожидается список моделей"})
        source = None
        if request.data.get("source_message"):
            source = (
                visible_messages(conversation).filter(pk=request.data["source_message"]).first()
            )
            if source is None:
                raise APIValidationError({"source_message": "Сообщение не найдено"})
        return prompt, model_slugs, source

    @action(detail=True, methods=["post"], url_path="compare/preview")
    def compare_cost_preview(self, request, pk=None):
        conversation = self.get_object()
        prompt, model_slugs, _source = self._compare_payload(request, conversation)
        try:
            preview = compare_preview(prompt=prompt, model_slugs=model_slugs)
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        return Response(
            {
                "expected_min_rub": str(preview["expected_min_rub"]),
                "expected_max_rub": str(preview["expected_max_rub"]),
                "confirmation_required": preview["confirmation_required"],
                "confirmation_threshold_rub": str(preview["confirmation_threshold_rub"]),
                "models": [
                    {
                        "model": row["model"].slug,
                        "display_name": row["model"].display_name,
                        "expected_min_rub": str(row["minimum"].user_charge_rub),
                        "expected_max_rub": str(row["maximum"].user_charge_rub),
                    }
                    for row in preview["models"]
                ],
            }
        )

    @action(detail=True, methods=["post"], url_path="compare")
    def compare_models(self, request, pk=None):
        conversation = self.get_object()
        key = request.headers.get("Idempotency-Key", "")
        if not key or len(key) > 160:
            raise APIValidationError({"detail": "Корректный Idempotency-Key обязателен"})
        prompt, model_slugs, source = self._compare_payload(request, conversation)
        try:
            run = run_compare(
                user=request.user,
                conversation=conversation,
                prompt=prompt,
                model_slugs=model_slugs,
                idempotency_key=key,
                source_message=source,
                confirmed=request.data.get("confirm_cost") is True,
            )
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        return Response(serialize_compare(run))

    @action(
        detail=True,
        methods=["get"],
        url_path=r"compare/(?P<compare_id>[^/.]+)",
    )
    def compare_detail(self, request, pk=None, compare_id=None):
        run = self.get_object().compare_runs.filter(pk=compare_id).first()
        if run is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_compare(run))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"compare/(?P<compare_id>[^/.]+)/synthesize",
    )
    def compare_synthesis(self, request, pk=None, compare_id=None):
        run = self.get_object().compare_runs.filter(pk=compare_id).first()
        if run is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            synthesize_compare(
                user=request.user,
                compare_run=run,
                model_slug=request.data.get("model") or run.model_slugs[0],
                confirmed=request.data.get("confirm_cost") is True,
            )
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        return Response(serialize_compare(run))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"compare/(?P<compare_id>[^/.]+)/variants/(?P<variant_id>[^/.]+)/branch",
    )
    def compare_branch(self, request, pk=None, compare_id=None, variant_id=None):
        conversation = self.get_object()
        variant = CompareVariant.objects.filter(
            pk=variant_id,
            compare_run_id=compare_id,
            compare_run__conversation=conversation,
            state=CompareVariant.State.COMPLETED,
        ).first()
        if variant is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            branch_from_variant(
                user=request.user,
                variant=variant,
                title=request.data.get("title") or f"{variant.model.display_name}: вариант",
            )
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        conversation.refresh_from_db()
        return Response(self.get_serializer(conversation).data)

    @action(detail=True, methods=["get", "put", "delete"])
    def draft(self, request, pk=None):
        conversation = self.get_object()
        if request.method == "GET":
            draft = ConversationDraft.objects.filter(conversation=conversation).first()
            if draft is None:
                return Response({"content": "", "version": 0, "updated_at": None})
            return Response(ConversationDraftSerializer(draft).data)
        if request.method == "DELETE":
            ConversationDraft.objects.filter(conversation=conversation).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = ConversationDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            draft = (
                ConversationDraft.objects.select_for_update()
                .filter(conversation=conversation)
                .first()
            )
            if draft is None:
                draft = ConversationDraft.objects.create(
                    conversation=conversation,
                    content=serializer.validated_data["content"],
                )
            else:
                draft.content = serializer.validated_data["content"]
                draft.version += 1
                draft.save(update_fields=["content", "version", "updated_at"])
        return Response(ConversationDraftSerializer(draft).data)

    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key")
        if not key or len(key) > 160:
            return Response(
                {"detail": "Idempotency-Key обязателен"}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            generation = generate_reply(
                user=request.user,
                conversation=self.get_object(),
                idempotency_key=key,
                **serializer.validated_data,
            )
        except (ValidationError, AIModel.DoesNotExist) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "generation_id": generation.id,
                "state": generation.state,
                "message": ConversationSerializer(self.get_object()).data["messages"][-1],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="messages/stream")
    def stream_messages(self, request, pk=None):
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = request.headers.get("Idempotency-Key")
        if not key or len(key) > 160:
            return Response(
                {"detail": "Idempotency-Key обязателен"}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            generation, _created = prepare(
                user=request.user,
                conversation=self.get_object(),
                idempotency_key=key,
                **serializer.validated_data,
            )
        except (ValidationError, AIModel.DoesNotExist) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        response = StreamingHttpResponse(
            run(generation), content_type="text/event-stream; charset=utf-8"
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response
