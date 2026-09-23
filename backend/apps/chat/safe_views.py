from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.response import Response

from apps.ai_registry.models import AIModel

from .compare import branch_from_variant, compare_preview, run_compare, synthesize_compare
from .models import CompareVariant, Conversation
from .public_compare import public_model_identity, public_preview_rows, serialize_compare_public
from .ux_models import ConversationUIState
from .views import ConversationViewSet


def _assert_public_models(slugs):
    if AIModel.objects.filter(slug__in=slugs, provider__slug="gigachat").exists():
        raise APIValidationError(
            {"models": "Внутренние модели недоступны для прямого выбора. Используйте System Lite, System Pro или System Max в обычном чате."}
        )


def _assert_public_model(slug):
    if AIModel.objects.filter(slug=slug, provider__slug="gigachat").exists():
        raise APIValidationError(
            {"model": "Внутренняя модель недоступна для прямого выбора."}
        )


class SafeConversationViewSet(ConversationViewSet):
    """Conversation API with non-destructive deletion for billing/audit safety."""

    def get_queryset(self):
        visible = Q(ui_state__deleted_at__isnull=True) | Q(ui_state__isnull=True)
        if self.action == "destroy":
            return Conversation.objects.filter(owner=self.request.user).filter(visible)
        return super().get_queryset().filter(visible)

    def perform_destroy(self, instance):
        state, _ = ConversationUIState.objects.get_or_create(
            conversation=instance,
            defaults={"owner": self.request.user},
        )
        state.deleted_at = timezone.now()
        state.is_pinned = False
        state.folder = None
        state.save(update_fields=["deleted_at", "is_pinned", "folder", "updated_at"])

    @action(detail=True, methods=["post"], url_path="compare/preview")
    def compare_cost_preview(self, request, pk=None):
        conversation = self.get_object()
        prompt, model_slugs, _source = self._compare_payload(request, conversation)
        _assert_public_models(model_slugs)
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
                "models": public_preview_rows(preview),
            }
        )

    @action(detail=True, methods=["post"], url_path="compare")
    def compare_models(self, request, pk=None):
        conversation = self.get_object()
        key = request.headers.get("Idempotency-Key", "")
        if not key or len(key) > 160:
            raise APIValidationError({"detail": "Корректный Idempotency-Key обязателен"})
        prompt, model_slugs, source = self._compare_payload(request, conversation)
        _assert_public_models(model_slugs)
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
        return Response(serialize_compare_public(run))

    @action(
        detail=True,
        methods=["get"],
        url_path=r"compare/(?P<compare_id>[^/.]+)",
    )
    def compare_detail(self, request, pk=None, compare_id=None):
        run = self.get_object().compare_runs.filter(pk=compare_id).first()
        if run is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(serialize_compare_public(run))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"compare/(?P<compare_id>[^/.]+)/synthesize",
    )
    def compare_synthesis(self, request, pk=None, compare_id=None):
        run = self.get_object().compare_runs.filter(pk=compare_id).first()
        if run is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        model_slug = request.data.get("model") or (run.model_slugs[0] if run.model_slugs else "")
        _assert_public_model(model_slug)
        try:
            synthesize_compare(
                user=request.user,
                compare_run=run,
                model_slug=model_slug,
                confirmed=request.data.get("confirm_cost") is True,
            )
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        return Response(serialize_compare_public(run))

    @action(
        detail=True,
        methods=["post"],
        url_path=r"compare/(?P<compare_id>[^/.]+)/variants/(?P<variant_id>[^/.]+)/branch",
    )
    def compare_branch(self, request, pk=None, compare_id=None, variant_id=None):
        conversation = self.get_object()
        variant = (
            CompareVariant.objects.select_related("model__provider")
            .filter(
                pk=variant_id,
                compare_run_id=compare_id,
                compare_run__conversation=conversation,
                state=CompareVariant.State.COMPLETED,
            )
            .first()
        )
        if variant is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        public_name = public_model_identity(variant.model)["model_name"]
        try:
            branch_from_variant(
                user=request.user,
                variant=variant,
                title=request.data.get("title") or f"{public_name}: вариант",
            )
        except ValidationError as exc:
            raise APIValidationError({"detail": exc.messages}) from exc
        conversation.refresh_from_db()
        return Response(self.get_serializer(conversation).data)
