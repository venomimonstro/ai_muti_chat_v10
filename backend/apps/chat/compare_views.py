from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from .compare import compare_preview, run_compare, serialize_compare, synthesize_compare
from .models import CompareRun, Conversation


def _detail(exc):
    messages = getattr(exc, "messages", None)
    return messages[0] if messages else str(exc)


def _conversation(user, conversation_id):
    return get_object_or_404(
        Conversation.objects.filter(pk=conversation_id, owner=user).filter(
            Q(ui_state__isnull=True) | Q(ui_state__deleted_at__isnull=True)
        )
    )


def _models(request):
    value = request.data.get("models", [])
    if not isinstance(value, list):
        raise DjangoValidationError("Список моделей имеет неверный формат")
    return [str(item).strip() for item in value if str(item).strip()]


class ComparePreviewView(APIView):
    def post(self, request):
        prompt = str(request.data.get("prompt", "")).strip()
        if not prompt or len(prompt) > 100_000:
            return Response({"detail": "Введите запрос длиной до 100000 символов"}, status=400)
        try:
            preview = compare_preview(prompt=prompt, model_slugs=_models(request))
        except DjangoValidationError as exc:
            return Response({"detail": _detail(exc)}, status=400)
        return Response(
            {
                "expected_min_rub": str(preview["expected_min_rub"]),
                "expected_max_rub": str(preview["expected_max_rub"]),
                "confirmation_required": preview["confirmation_required"],
                "confirmation_threshold_rub": str(preview["confirmation_threshold_rub"]),
                "models": [
                    {
                        "model": row["model"].slug,
                        "model_name": row["model"].display_name,
                        "provider": row["model"].provider.slug,
                        "expected_min_rub": str(row["minimum"].user_charge_rub),
                        "expected_max_rub": str(row["maximum"].user_charge_rub),
                    }
                    for row in preview["models"]
                ],
            }
        )


class CompareRunView(APIView):
    def post(self, request):
        key = request.headers.get("Idempotency-Key", "")
        prompt = str(request.data.get("prompt", "")).strip()
        conversation_id = request.data.get("conversation_id")
        if not conversation_id:
            return Response({"detail": "Не указан чат для сохранения сравнения"}, status=400)
        conversation = _conversation(request.user, conversation_id)
        try:
            run = run_compare(
                user=request.user,
                conversation=conversation,
                prompt=prompt,
                model_slugs=_models(request),
                idempotency_key=key,
                confirmed=request.data.get("confirm_cost") is True,
            )
        except DjangoValidationError as exc:
            return Response({"detail": _detail(exc)}, status=400)
        return Response(serialize_compare(run))


class CompareDetailView(APIView):
    def get(self, request, compare_id):
        run = get_object_or_404(
            CompareRun.objects.prefetch_related("variants__model__provider"),
            pk=compare_id,
            owner=request.user,
        )
        return Response(serialize_compare(run))


class CompareSynthesisView(APIView):
    def post(self, request, compare_id):
        run = get_object_or_404(CompareRun, pk=compare_id, owner=request.user)
        model_slug = str(request.data.get("model", "")).strip()
        if not model_slug:
            return Response({"detail": "Выберите модель для итогового ответа"}, status=400)
        try:
            run = synthesize_compare(
                user=request.user,
                compare_run=run,
                model_slug=model_slug,
                confirmed=request.data.get("confirm_cost") is True,
            )
        except DjangoValidationError as exc:
            return Response({"detail": _detail(exc)}, status=400)
        return Response(serialize_compare(run))
