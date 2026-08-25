import math
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.billing.pricing import active_price, quote
from apps.evals.models import EvalCase, EvalRun, ModelScore
from apps.files.models import FileAsset

from .models import AIModel, Provider, RoutingPolicyVersion
from .reliability import candidate_models, provider_available

OUTPUT_TOKENS = 1024
MODE_LABELS = {
    "manual": "Вручную",
    "economy": "Эконом",
    "balanced": "Баланс",
    "maximum": "Максимум",
}
TASK_LABELS = dict(EvalCase.Taxonomy.choices)
DEFAULT_WEIGHTS = {
    "economy": {"quality": 0.25, "cost": 0.55, "latency": 0.15, "health": 0.05},
    "balanced": {"quality": 0.50, "cost": 0.25, "latency": 0.20, "health": 0.05},
    "maximum": {"quality": 0.75, "cost": 0.05, "latency": 0.15, "health": 0.05},
}
DEFAULT_THRESHOLDS = {
    "default_quality": 0.55,
    "economy_min_quality": 0.60,
    "fallback_price_multiplier": 1.50,
    "unknown_latency_ms": 1500,
}
RULES = [
    (EvalCase.Taxonomy.DEBUGGING, ("ошибк", "баг", "debug", "traceback", "исправь код")),
    (EvalCase.Taxonomy.CODING, ("напиши код", "функци", "python", "javascript", "sql", "api")),
    (EvalCase.Taxonomy.SPREADSHEETS, ("excel", "таблиц", "формул", "ячейк", "xlsx", "csv")),
    (EvalCase.Taxonomy.SEO, ("seo", "семантик", "title", "description", "поисков")),
    (EvalCase.Taxonomy.MARKETING, ("маркетинг", "реклам", "конверси", "воронк", "cac", "romi")),
    (EvalCase.Taxonomy.TRANSLATION, ("переведи", "перевод", "translate")),
    (EvalCase.Taxonomy.EXTRACTION, ("извлеки", "вытащи факт", "json", "распознай поля")),
    (EvalCase.Taxonomy.STRUCTURING, ("структурируй", "разбей по", "составь таблицу", "план")),
    (
        EvalCase.Taxonomy.COPYWRITING,
        ("напиши текст", "напиши реклам", "оффер", "пост", "статью", "продающ"),
    ),
    (EvalCase.Taxonomy.EDITING, ("исправь текст", "отредакт", "перепиши", "сократи")),
    (EvalCase.Taxonomy.RESEARCH, ("исследуй", "найди акту", "источник", "сравни рынок")),
    (EvalCase.Taxonomy.REASONING, ("почему", "рассчитай", "задач", "логик", "обоснуй")),
    (EvalCase.Taxonomy.RUSSIAN_STYLE, ("по-русски", "стилист", "канцеляр", "грамотн")),
]


@dataclass(frozen=True)
class TaskClassification:
    taxonomy: str
    confidence: float
    required_capabilities: list[str]
    signals: dict


@dataclass(frozen=True)
class RouteSelection:
    policy: RoutingPolicyVersion
    classification: TaskClassification
    selected: AIModel
    ordered_models: list[AIModel]
    candidates: list[dict]
    explanation: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_rub: Decimal


def classify_task(content, conversation):
    normalized = content.casefold()
    matches = []
    for taxonomy, needles in RULES:
        hits = sum(needle in normalized for needle in needles)
        if hits:
            matches.append((hits, taxonomy))
    matches.sort(reverse=True)
    taxonomy = matches[0][1] if matches else EvalCase.Taxonomy.QA
    confidence = min(0.98, 0.58 + (matches[0][0] * 0.12)) if matches else 0.52
    long_context = len(content) > 8000 or any(
        token in normalized for token in ("длинный документ", "весь документ", "большой файл")
    )
    if long_context and not matches:
        taxonomy = EvalCase.Taxonomy.LONG_DOCUMENTS
        confidence = 0.82
    has_project_files = bool(
        conversation.project_id
        and FileAsset.objects.filter(
            project_id=conversation.project_id,
            status__in=[FileAsset.Status.READY, FileAsset.Status.PARTIAL],
            deleted_at__isnull=True,
        ).exists()
    )
    has_visual_files = bool(
        conversation.project_id
        and FileAsset.objects.filter(
            project_id=conversation.project_id,
            status__in=[FileAsset.Status.READY, FileAsset.Status.PARTIAL],
            detected_type__in=["png", "jpeg", "webp"],
            deleted_at__isnull=True,
        ).exists()
    )
    image_request = any(
        token in normalized for token in ("изображен", "фото", "картин", "скриншот")
    )
    needs_vision = image_request and has_visual_files
    needs_tools = any(
        token in normalized
        for token in ("найди акту", "проверь в интернете", "сегодня", "последние новости")
    )
    capabilities = ["text"]
    if needs_vision:
        capabilities.append("vision")
    return TaskClassification(
        taxonomy=taxonomy,
        confidence=confidence,
        required_capabilities=capabilities,
        signals={
            "long_context": long_context,
            "has_project_files": has_project_files,
            "has_visual_files": has_visual_files,
            "needs_vision": needs_vision,
            "needs_tools": needs_tools,
            "matched_rules": matches[:3],
        },
    )


def _capabilities(model):
    return set(model.capabilities or ["text", "streaming"])


def _quality(model, taxonomy, default):
    score = (
        ModelScore.objects.filter(
            model=model,
            taxonomy=taxonomy,
            run__state=EvalRun.State.COMPLETED,
            run__gate_status=EvalRun.Gate.PASSED,
        )
        .order_by("-run__completed_at", "-created_at")
        .first()
    )
    return (float(score.score), "eval", str(score.run_id)) if score else (default, "default", None)


def _estimated_input(conversation, content):
    recent = list(conversation.messages.exclude(content="").order_by("-created_at")[:13])
    total = sum(len(item.content) for item in recent)
    if not recent or recent[0].role != "user" or recent[0].content != content:
        total += len(content)
    return max(128, total + 32)


def _health_score(provider):
    return {
        Provider.HealthState.HEALTHY: 1.0,
        Provider.HealthState.UNKNOWN: 0.75,
        Provider.HealthState.DEGRADED: 0.4,
    }.get(provider.health_state, 0.0)


def _normalize_inverse(value, minimum, maximum):
    if maximum <= minimum:
        return 1.0
    return 1 - ((value - minimum) / (maximum - minimum))


def _active_policy():
    policy = RoutingPolicyVersion.objects.filter(active=True).first()
    if not policy:
        policy, _created = RoutingPolicyVersion.objects.get_or_create(
            version="router-v1",
            defaults={
                "active": True,
                "mode_weights": DEFAULT_WEIGHTS,
                "thresholds": DEFAULT_THRESHOLDS,
            },
        )
        if not policy.active:
            raise ValidationError("Активная политика AUTO Router не настроена")
    return policy


def select_route(*, conversation, content):
    policy = _active_policy()
    classification = classify_task(content, conversation)
    input_tokens = _estimated_input(conversation, content)
    mode = conversation.routing_mode
    if mode == "manual":
        try:
            primary = AIModel.objects.select_related(
                "provider", "fallback_model", "current_version"
            ).get(
                slug=conversation.selected_model, enabled=True
            )
        except AIModel.DoesNotExist as exc:
            raise ValidationError("Выбранная модель недоступна") from exc
        available = candidate_models(primary)
        if not available:
            raise ValidationError("Выбранная модель временно недоступна")
        priced = []
        primary_cost = None
        multiplier = Decimal(str(policy.thresholds.get("fallback_price_multiplier", 1.5)))
        for model in available:
            price = active_price(model.slug)
            price_quote = quote(
                price,
                input_tokens,
                OUTPUT_TOKENS,
                provider_slug=model.provider.slug,
                model_slug=model.slug,
            )
            charge = price_quote.user_charge_rub
            if primary_cost is None:
                primary_cost = charge
            allowed = price_quote.margin_allowed and charge <= primary_cost * multiplier
            reasons = []
            if not price_quote.margin_allowed:
                reasons.append("margin_below_floor")
            if charge > primary_cost * multiplier:
                reasons.append("fallback_price_requires_consent")
            priced.append(
                {
                    "model": model.slug,
                    "provider": model.provider.slug,
                    "model_version": (
                        model.current_version.version if model.current_version else None
                    ),
                    "exact_api_id": model.upstream_model,
                    "status": "eligible" if allowed else "rejected",
                    "reasons": reasons,
                    "estimated_cost_rub": str(charge),
                    "gross_margin_percent": str(price_quote.gross_margin_percent),
                    "score": None,
                }
            )
        allowed_models = [
            model for model, item in zip(available, priced, strict=True) if item["status"] == "eligible"
        ]
        for rank, item in enumerate((item for item in priced if item["status"] == "eligible"), 1):
            item["rank"] = rank
            item["fallback_allowed"] = True
        return RouteSelection(
            policy=policy,
            classification=classification,
            selected=primary,
            ordered_models=allowed_models,
            candidates=priced,
            explanation=f"Модель {primary.display_name} выбрана пользователем вручную.",
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=OUTPUT_TOKENS,
            estimated_cost_rub=primary_cost,
        )

    weights = policy.mode_weights.get(mode)
    if not weights:
        raise ValidationError("Неизвестный режим AUTO Router")
    default_quality = float(policy.thresholds.get("default_quality", 0.55))
    economy_min = float(policy.thresholds.get("economy_min_quality", 0.60))
    unknown_latency = int(policy.thresholds.get("unknown_latency_ms", 1500))
    candidates = []
    model_lookup = {}
    for model in AIModel.objects.filter(enabled=True).select_related(
        "provider", "current_version"
    ):
        model_lookup[model.slug] = model
        reasons = []
        capabilities = _capabilities(model)
        missing = set(classification.required_capabilities) - capabilities
        if missing:
            reasons.append("missing_capabilities:" + ",".join(sorted(missing)))
        if not provider_available(model.provider):
            reasons.append("provider_unavailable")
        if len(content) + OUTPUT_TOKENS + 64 > model.context_window:
            reasons.append("context_window_too_small")
        try:
            price = active_price(model.slug)
            price_quote = quote(
                price,
                input_tokens,
                OUTPUT_TOKENS,
                provider_slug=model.provider.slug,
                model_slug=model.slug,
            )
            charge = price_quote.user_charge_rub
            if not price_quote.margin_allowed:
                reasons.append("margin_below_floor")
        except ValidationError:
            charge = None
            reasons.append("price_not_configured")
        quality, quality_source, eval_run = _quality(
            model, classification.taxonomy, default_quality
        )
        if mode == "economy" and quality_source == "eval" and quality < economy_min:
            reasons.append("quality_below_economy_minimum")
        latency = model.provider.last_latency_ms or unknown_latency
        candidates.append(
            {
                "model": model.slug,
                "provider": model.provider.slug,
                "model_version": (
                    model.current_version.version if model.current_version else None
                ),
                "exact_api_id": model.upstream_model,
                "status": "rejected" if reasons else "eligible",
                "reasons": reasons,
                "quality": quality,
                "quality_source": quality_source,
                "eval_run": eval_run,
                "latency_ms": latency,
                "health": model.provider.health_state,
                "context_window": model.context_window,
                "estimated_cost_rub": str(charge) if charge is not None else None,
                "gross_margin_percent": (
                    str(price_quote.gross_margin_percent) if charge is not None else None
                ),
                "score": None,
            }
        )
    eligible = [item for item in candidates if item["status"] == "eligible"]
    if not eligible:
        raise ValidationError("AUTO Router не нашёл подходящую доступную модель")
    costs = [float(item["estimated_cost_rub"]) for item in eligible]
    latencies = [item["latency_ms"] for item in eligible]
    contexts = [math.log2(item["context_window"]) for item in eligible]
    for item in eligible:
        cost_score = _normalize_inverse(float(item["estimated_cost_rub"]), min(costs), max(costs))
        latency_score = _normalize_inverse(item["latency_ms"], min(latencies), max(latencies))
        context_score = (
            (math.log2(item["context_window"]) - min(contexts)) / (max(contexts) - min(contexts))
            if max(contexts) > min(contexts)
            else 1.0
        )
        health_score = _health_score(model_lookup[item["model"]].provider)
        tag_bonus = 0.05 if classification.taxonomy in model_lookup[item["model"]].routing_tags else 0
        needs_bonus = 0.03 if classification.signals["needs_tools"] and "tools" in _capabilities(model_lookup[item["model"]]) else 0
        long_bonus = 0.05 * context_score if classification.signals["long_context"] else 0
        item["score_components"] = {
            "quality": round(item["quality"], 4),
            "cost": round(cost_score, 4),
            "latency": round(latency_score, 4),
            "health": round(health_score, 4),
            "context": round(context_score, 4),
            "tag_bonus": tag_bonus,
            "needs_bonus": needs_bonus,
            "long_context_bonus": round(long_bonus, 4),
        }
        item["score"] = round(
            item["quality"] * float(weights.get("quality", 0))
            + cost_score * float(weights.get("cost", 0))
            + latency_score * float(weights.get("latency", 0))
            + health_score * float(weights.get("health", 0))
            + tag_bonus
            + needs_bonus
            + long_bonus,
            6,
        )
    eligible.sort(key=lambda item: (-item["score"], item["model"]))
    selected_item = eligible[0]
    selected = model_lookup[selected_item["model"]]
    selected_cost = Decimal(selected_item["estimated_cost_rub"])
    multiplier = Decimal(str(policy.thresholds.get("fallback_price_multiplier", 1.5)))
    ordered_models = []
    for rank, item in enumerate(eligible, 1):
        item["rank"] = rank
        allowed = Decimal(item["estimated_cost_rub"]) <= selected_cost * multiplier
        item["fallback_allowed"] = allowed
        if allowed:
            ordered_models.append(model_lookup[item["model"]])
    label = TASK_LABELS.get(classification.taxonomy, classification.taxonomy)
    quality_note = (
        f"eval-оценка {selected_item['quality']:.0%}"
        if selected_item["quality_source"] == "eval"
        else "базовая оценка до накопления eval"
    )
    explanation = (
        f"AUTO определил задачу «{label}» и выбрал {selected.display_name}: "
        f"{quality_note}, провайдер {selected.provider.get_health_state_display().lower()}, "
        f"режим «{MODE_LABELS[mode]}»."
    )
    return RouteSelection(
        policy=policy,
        classification=classification,
        selected=selected,
        ordered_models=ordered_models,
        candidates=candidates,
        explanation=explanation,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=OUTPUT_TOKENS,
        estimated_cost_rub=selected_cost,
    )
