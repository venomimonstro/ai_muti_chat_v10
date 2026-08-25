import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.ai_registry.models import AIModel
from apps.ai_registry.reliability import provider_available
from apps.billing.pricing import active_price, calculate_from_snapshot, quote, require_margin
from apps.billing.services import release, reserve, settle
from apps.workspace_search.embeddings import index_message

from .branches import ensure_active_branch, fork_branch, visible_messages
from .models import CompareRun, CompareVariant, Message


def _models(slugs):
    unique = list(dict.fromkeys(slugs))
    if len(unique) < 2 or len(unique) > settings.COMPARE_MAX_MODELS:
        raise ValidationError(f"Выберите от 2 до {settings.COMPARE_MAX_MODELS} моделей")
    models = {
        item.slug: item
        for item in AIModel.objects.filter(slug__in=unique, enabled=True).select_related("provider")
    }
    ordered = []
    for slug in unique:
        model = models.get(slug)
        if not model or not provider_available(model.provider) or "text" not in model.capabilities:
            raise ValidationError(f"Модель {slug} недоступна для Compare")
        ordered.append(model)
    return ordered


def _one_model(slug):
    model = AIModel.objects.filter(slug=slug, enabled=True).select_related("provider").first()
    if not model or not provider_available(model.provider) or "text" not in model.capabilities:
        raise ValidationError(f"Модель {slug} недоступна")
    return model


def compare_preview(*, prompt, model_slugs):
    models = _models(model_slugs)
    input_tokens = len(prompt) + 64
    rows = []
    minimum = Decimal("0")
    maximum = Decimal("0")
    for model in models:
        max_output = min(settings.COMPARE_MAX_OUTPUT_TOKENS, model.max_output_tokens)
        if input_tokens + max_output + 32 > model.context_window:
            raise ValidationError(f"Контекст модели {model.slug} слишком мал для Compare")
        price = active_price(model.slug)
        low = require_margin(
            quote(
                price,
                input_tokens,
                128,
                provider_slug=model.provider.slug,
                model_slug=model.slug,
                operation_type="compare",
            )
        )
        high = require_margin(
            quote(
                price,
                input_tokens,
                max_output,
                provider_slug=model.provider.slug,
                model_slug=model.slug,
                operation_type="compare",
            )
        )
        minimum += low.user_charge_rub
        maximum += high.user_charge_rub
        rows.append(
            {
                "model": model,
                "price": price,
                "minimum": low,
                "maximum": high,
            }
        )
    threshold = Decimal(settings.COMPARE_CONFIRM_THRESHOLD_RUB)
    return {
        "models": rows,
        "expected_min_rub": minimum,
        "expected_max_rub": maximum,
        "confirmation_required": maximum >= threshold,
        "confirmation_threshold_rub": threshold,
    }


def _provider_call(model, messages):
    started = time.monotonic()
    text = ""
    completed = None
    for event in adapter_for(model).stream(
        model=model.upstream_model,
        messages=messages,
        max_output_tokens=min(settings.COMPARE_MAX_OUTPUT_TOKENS, model.max_output_tokens),
    ):
        if event.kind == "delta":
            text += event.text_delta
        else:
            completed = event
    if completed is None:
        raise ProviderError("Compare stream ended without usage", code="invalid_stream")
    return text.strip(), completed, int((time.monotonic() - started) * 1000)


def run_compare(
    *,
    user,
    conversation,
    prompt,
    model_slugs,
    idempotency_key,
    source_message=None,
    confirmed=False,
):
    if not settings.COMPARE_ENABLED:
        raise ValidationError("Compare временно отключён")
    existing = CompareRun.objects.filter(
        idempotency_key=idempotency_key, conversation__owner=user
    ).first()
    if existing:
        return existing
    preview = compare_preview(prompt=prompt, model_slugs=model_slugs)
    if preview["confirmation_required"] and not confirmed:
        raise ValidationError("Подтвердите ожидаемую стоимость Compare")
    branch = ensure_active_branch(conversation, user)
    run = CompareRun.objects.create(
        conversation=conversation,
        branch=branch,
        source_message=source_message,
        prompt=prompt,
        idempotency_key=idempotency_key,
        state=CompareRun.State.RUNNING,
        model_slugs=[row["model"].slug for row in preview["models"]],
        expected_min_rub=preview["expected_min_rub"],
        expected_max_rub=preview["expected_max_rub"],
    )
    reservation = reserve(user, preview["expected_max_rub"], f"compare:{run.id}")
    run.reservation_id = reservation.id
    run.save(update_fields=["reservation_id"])
    variants = []
    for position, row in enumerate(preview["models"]):
        variant = CompareVariant.objects.create(
            compare_run=run,
            model=row["model"],
            position=position,
            state=CompareVariant.State.RUNNING,
            expected_min_rub=row["minimum"].user_charge_rub,
            expected_max_rub=row["maximum"].user_charge_rub,
            pricing_snapshot=row["maximum"].pricing_snapshot,
        )
        variants.append((variant, row))
    messages = [{"role": "user", "content": prompt}]
    futures = {}
    with ThreadPoolExecutor(max_workers=len(variants), thread_name_prefix="compare") as pool:
        for variant, row in variants:
            futures[pool.submit(_provider_call, row["model"], messages)] = (variant, row)
        for future in as_completed(futures):
            variant, row = futures[future]
            try:
                output, usage, latency = future.result()
                provider_cost, charge, _profit, _margin = calculate_from_snapshot(
                    row["price"],
                    usage.input_tokens,
                    usage.output_tokens,
                    variant.pricing_snapshot,
                )
                variant.state = CompareVariant.State.COMPLETED
                variant.output = output
                variant.provider_request_id = usage.provider_request_id
                variant.input_tokens = usage.input_tokens
                variant.output_tokens = usage.output_tokens
                variant.actual_cost_rub = charge
                variant.provider_cost_rub = provider_cost
                variant.latency_ms = latency
            except Exception as exc:
                variant.state = CompareVariant.State.FAILED
                variant.error_code = (
                    exc.code if isinstance(exc, ProviderError) else "compare_failed"
                )
            variant.completed_at = timezone.now()
            variant.save()
    actual = sum((variant.actual_cost_rub for variant, _row in variants), Decimal("0"))
    settle(reservation.id, actual)
    success_count = sum(variant.state == CompareVariant.State.COMPLETED for variant, _ in variants)
    run.actual_cost_rub = actual
    run.state = (
        CompareRun.State.COMPLETED
        if success_count == len(variants)
        else CompareRun.State.PARTIAL
        if success_count
        else CompareRun.State.FAILED
    )
    run.completed_at = timezone.now()
    run.save(update_fields=["actual_cost_rub", "state", "completed_at"])
    return run


def synthesize_compare(*, user, compare_run, model_slug, confirmed=False):
    if compare_run.synthesis_output:
        return compare_run
    if compare_run.synthesis_reservation_id:
        raise ValidationError("Предыдущая попытка синтеза уже завершилась ошибкой")
    variants = list(compare_run.variants.filter(state=CompareVariant.State.COMPLETED))
    if len(variants) < 2:
        raise ValidationError("Для синтеза нужны минимум два успешных ответа")
    model = _one_model(model_slug)
    prompt = "Сформируй лучший итоговый ответ на исходный запрос, используя варианты ниже.\n\n"
    prompt += f"Исходный запрос: {compare_run.prompt}\n\n"
    prompt += "\n\n".join(f"Вариант {item.position + 1}:\n{item.output}" for item in variants)
    max_output = min(settings.COMPARE_MAX_OUTPUT_TOKENS, model.max_output_tokens)
    if len(prompt) + max_output + 32 > model.context_window:
        raise ValidationError("Ответы Compare не помещаются в контекст модели синтеза")
    price = active_price(model.slug)
    expected = require_margin(
        quote(
            price,
            len(prompt) + 64,
            max_output,
            provider_slug=model.provider.slug,
            model_slug=model.slug,
            operation_type="compare_synthesis",
        )
    )
    if (
        expected.user_charge_rub >= Decimal(settings.COMPARE_CONFIRM_THRESHOLD_RUB)
        and not confirmed
    ):
        raise ValidationError("Подтвердите ожидаемую стоимость синтеза")
    reservation = reserve(user, expected.user_charge_rub, f"compare-synthesis:{compare_run.id}")
    compare_run.synthesis_reservation_id = reservation.id
    compare_run.synthesis_model_slug = model.slug
    compare_run.synthesis_pricing_snapshot = expected.pricing_snapshot
    compare_run.save(
        update_fields=[
            "synthesis_reservation_id",
            "synthesis_model_slug",
            "synthesis_pricing_snapshot",
        ]
    )
    try:
        output, usage, _latency = _provider_call(model, [{"role": "user", "content": prompt}])
        _provider_cost, charge, _profit, _margin = calculate_from_snapshot(
            price, usage.input_tokens, usage.output_tokens, expected.pricing_snapshot
        )
        settle(reservation.id, charge)
        compare_run.synthesis_output = output
        compare_run.synthesis_cost_rub = charge
        compare_run.save(update_fields=["synthesis_output", "synthesis_cost_rub"])
    except Exception:
        release(reservation.id)
        raise
    return compare_run


@transaction.atomic
def branch_from_variant(*, user, variant, title="Ветка из Compare"):
    run = variant.compare_run
    source = run.source_message or visible_messages(run.conversation).order_by("created_at").last()
    if source is None:
        raise ValidationError("Нет исходного сообщения для ветвления")
    branch = fork_branch(
        conversation=run.conversation, user=user, source_message=source, title=title
    )
    message = Message.objects.create(
        conversation=run.conversation,
        branch=branch,
        role=Message.Role.ASSISTANT,
        status=Message.Status.COMPLETED,
        content=variant.output,
    )
    index_message(message)
    return branch


def serialize_compare(run):
    return {
        "id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "branch_id": str(run.branch_id) if run.branch_id else None,
        "source_message_id": str(run.source_message_id) if run.source_message_id else None,
        "prompt": run.prompt,
        "state": run.state,
        "models": run.model_slugs,
        "expected_min_rub": str(run.expected_min_rub),
        "expected_max_rub": str(run.expected_max_rub),
        "actual_cost_rub": str(run.actual_cost_rub),
        "synthesis_model": run.synthesis_model_slug,
        "synthesis_output": run.synthesis_output,
        "synthesis_cost_rub": str(run.synthesis_cost_rub),
        "variants": [
            {
                "id": str(item.id),
                "model": item.model.slug,
                "model_name": item.model.display_name,
                "provider": item.model.provider.slug,
                "state": item.state,
                "output": item.output,
                "expected_min_rub": str(item.expected_min_rub),
                "expected_max_rub": str(item.expected_max_rub),
                "actual_cost_rub": str(item.actual_cost_rub),
                "input_tokens": item.input_tokens,
                "output_tokens": item.output_tokens,
                "latency_ms": item.latency_ms,
                "error_code": item.error_code,
            }
            for item in run.variants.select_related("model__provider").all()
        ],
    }
