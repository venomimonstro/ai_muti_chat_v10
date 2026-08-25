import time
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.ai_registry.adapters import ProviderError, adapter_for
from apps.billing.pricing import active_price, calculate

from .judge import score_response
from .models import EvalCase, EvalResult, EvalRun, ModelScore


def _decimal(value):
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _gate(run, baseline=None):
    reasons = []
    if float(run.average_score) < settings.EVAL_MIN_AVERAGE_SCORE:
        reasons.append("average_score_below_minimum")
    if float(run.hallucination_rate) > settings.EVAL_MAX_HALLUCINATION_RATE:
        reasons.append("hallucination_rate_above_maximum")
    error_rate = run.error_count / run.case_count if run.case_count else 1
    if error_rate > settings.EVAL_MAX_ERROR_RATE:
        reasons.append("error_rate_above_maximum")
    if baseline:
        if baseline.state != EvalRun.State.COMPLETED:
            reasons.append("baseline_not_completed")
        elif baseline.dataset_version != run.dataset_version:
            reasons.append("baseline_dataset_mismatch")
        elif float(baseline.average_score) - float(run.average_score) > settings.EVAL_MAX_REGRESSION:
            reasons.append("quality_regression")
        for taxonomy, score in run.taxonomy_scorecard.items():
            baseline_score = baseline.taxonomy_scorecard.get(taxonomy)
            if baseline_score is not None and float(baseline_score) - float(score) > settings.EVAL_MAX_REGRESSION:
                reasons.append(f"taxonomy_regression:{taxonomy}")
    return (EvalRun.Gate.FAILED, reasons) if reasons else (EvalRun.Gate.PASSED, [])


def _aggregate(run):
    results = list(run.results.select_related("case"))
    count = len(results)
    grouped = defaultdict(list)
    for result in results:
        grouped[result.case.taxonomy].append(float(result.score))
    scorecard = {
        taxonomy: round(sum(scores) / len(scores), 4) for taxonomy, scores in grouped.items()
    }
    run.case_count = count
    run.passed_count = sum(result.passed for result in results)
    run.error_count = sum(bool(result.error_code) for result in results)
    run.average_score = _decimal(sum(float(item.score) for item in results) / count if count else 0)
    run.hallucination_rate = _decimal(
        sum(item.hallucinated for item in results) / count if count else 0
    )
    run.average_latency_ms = round(sum(item.latency_ms for item in results) / count) if count else 0
    run.total_cost_rub = sum((item.cost_rub for item in results), Decimal("0"))
    run.total_input_tokens = sum(item.input_tokens for item in results)
    run.total_output_tokens = sum(item.output_tokens for item in results)
    run.taxonomy_scorecard = scorecard
    successful = [item for item in results if not item.error_code]
    quality = {}
    for field in (
        "correctness",
        "instruction_following",
        "russian_quality",
        "verbosity_control",
    ):
        quality[field] = round(
            sum(float(getattr(item, field)) for item in successful) / len(successful), 4
        ) if successful else 0
    for key, subset in {
        "tool_reliability": [item for item in results if "tools" in item.case.tags],
        "file_handling": [item for item in results if "files" in item.case.tags],
        "long_context_stability": [
            item for item in results if item.case.taxonomy == EvalCase.Taxonomy.LONG_DOCUMENTS
        ],
    }.items():
        quality[key] = (
            round(sum(float(item.score) for item in subset) / len(subset), 4) if subset else None
        )
    run.quality_scorecard = quality
    return scorecard


def run_evaluation(
    *, model, dataset_version, baseline=None, adapter=None, limit=None, model_version=None
):
    cases = EvalCase.objects.filter(dataset_version=dataset_version, enabled=True).order_by(
        "taxonomy", "slug"
    )
    if limit:
        cases = cases[:limit]
    cases = list(cases)
    if not cases:
        raise ValueError("В выбранном eval dataset нет активных кейсов")
    if baseline and baseline.model_id != model.id:
        raise ValueError("Baseline должен принадлежать той же модели")
    if model_version and model_version.model_id != model.id:
        raise ValueError("ModelVersion должна принадлежать выбранной модели")

    exact_api_id = model_version.exact_api_id if model_version else model.upstream_model
    capabilities = model_version.capabilities if model_version else model.capabilities
    context_window = model_version.context_window if model_version else model.context_window
    max_output_tokens = (
        model_version.max_output_tokens if model_version else model.max_output_tokens
    )

    price = active_price(model.slug)
    run = EvalRun.objects.create(
        model=model,
        dataset_version=dataset_version,
        baseline=baseline,
        case_count=len(cases),
        model_version_id_snapshot=model_version.id if model_version else model.current_version_id,
        model_snapshot={
            "slug": model.slug,
            "upstream_model": model.upstream_model,
            "exact_api_id": exact_api_id,
            "model_version": model_version.version if model_version else None,
            "provider": model.provider.slug,
            "context_window": context_window,
            "max_output_tokens": max_output_tokens,
            "capabilities": capabilities,
        },
    )
    try:
        provider_adapter = adapter or adapter_for(model)
        for case in cases:
            messages = []
            if case.system_prompt:
                messages.append({"role": "system", "content": case.system_prompt})
            messages.append({"role": "user", "content": case.prompt})
            started = time.monotonic()
            try:
                result = provider_adapter.generate(
                    model=exact_api_id,
                    messages=messages,
                    max_output_tokens=min(max_output_tokens, settings.EVAL_MAX_OUTPUT_TOKENS),
                )
                latency_ms = int((time.monotonic() - started) * 1000)
                judged = score_response(result.text, case.rubric)
                provider_cost, _charge = calculate(
                    price, result.input_tokens, result.output_tokens
                )
                EvalResult.objects.create(
                    run=run,
                    case=case,
                    response=result.text,
                    case_snapshot={
                        "dataset_version": case.dataset_version,
                        "slug": case.slug,
                        "taxonomy": case.taxonomy,
                        "prompt": case.prompt,
                        "system_prompt": case.system_prompt,
                        "rubric": case.rubric,
                        "min_score": str(case.min_score),
                    },
                    score=_decimal(judged.score),
                    correctness=_decimal(judged.correctness),
                    instruction_following=_decimal(judged.instruction_following),
                    russian_quality=_decimal(judged.russian_quality),
                    verbosity_control=_decimal(judged.verbosity_control),
                    hallucinated=judged.hallucinated,
                    passed=judged.score >= float(case.min_score) and not judged.hallucinated,
                    latency_ms=latency_ms,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    cost_rub=provider_cost,
                    price_version_id_snapshot=price.id,
                    provider_request_id=result.provider_request_id,
                    judge_details=judged.details,
                )
            except Exception as exc:
                EvalResult.objects.create(
                    run=run,
                    case=case,
                    case_snapshot={
                        "dataset_version": case.dataset_version,
                        "slug": case.slug,
                        "taxonomy": case.taxonomy,
                        "prompt": case.prompt,
                        "system_prompt": case.system_prompt,
                        "rubric": case.rubric,
                        "min_score": str(case.min_score),
                    },
                    price_version_id_snapshot=price.id,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_code=exc.code if isinstance(exc, ProviderError) else "eval_case_failed",
                    judge_details={"error_type": type(exc).__name__},
                )

        scorecard = _aggregate(run)
        run.gate_status, run.gate_reasons = _gate(run, baseline)
        run.state = EvalRun.State.COMPLETED
        run.completed_at = timezone.now()
        run.save(
            update_fields=[
                "case_count",
                "passed_count",
                "error_count",
                "average_score",
                "hallucination_rate",
                "average_latency_ms",
                "total_cost_rub",
                "total_input_tokens",
                "total_output_tokens",
                "taxonomy_scorecard",
                "quality_scorecard",
                "gate_status",
                "gate_reasons",
                "state",
                "completed_at",
            ]
        )
        with transaction.atomic():
            ModelScore.objects.filter(run=run).delete()
            ModelScore.objects.bulk_create(
                [
                    ModelScore(
                        run=run,
                        model=model,
                        taxonomy=taxonomy,
                        score=_decimal(score),
                        case_count=len([case for case in cases if case.taxonomy == taxonomy]),
                        eligible_for_promotion=run.gate_status == EvalRun.Gate.PASSED,
                    )
                    for taxonomy, score in scorecard.items()
                ]
            )
        return run
    except Exception:
        run.state = EvalRun.State.FAILED
        run.gate_status = EvalRun.Gate.FAILED
        run.completed_at = timezone.now()
        run.gate_reasons = ["runner_failed"]
        run.save(update_fields=["state", "gate_status", "completed_at", "gate_reasons"])
        raise
