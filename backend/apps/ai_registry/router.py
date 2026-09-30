import math
import os
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from apps.billing.pricing import active_price, quote
from apps.evals.models import EvalCase, EvalRun, ModelScore
from apps.files.models import FileAsset
from apps.procurement.readiness import quote_has_procurement_capacity

from .models import AIModel, Provider, RoutingPolicyVersion
from .reliability import provider_available
from .token_estimator import estimate_text_tokens

OUTPUT_TOKENS = max(512, min(8192, int(os.getenv("CHAT_MAX_OUTPUT_TOKENS", "4096"))))
CONTEXT_SAFETY_TOKENS = 64
MODE_LABELS = {
    "auto": "AUTO",
    "manual": "Модель",
    "economy": "Простой",
    "balanced": "Средний",
    "maximum": "Сложный",
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
    "tier_models": {},
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
    (EvalCase.Taxonomy.COPYWRITING, ("напиши текст", "напиши реклам", "оффер", "пост", "статью", "продающ")),
    (EvalCase.Taxonomy.EDITING, ("исправь текст", "отредакт", "перепиши", "сократи")),
    (EvalCase.Taxonomy.RESEARCH, ("исследуй", "найди акту", "источник", "сравни рынок")),
    (EvalCase.Taxonomy.REASONING, ("почему", "рассчитай", "задач", "логик", "обоснуй")),
    (EvalCase.Taxonomy.RUSSIAN_STYLE, ("по-русски", "стилист", "канцеляр", "грамотн")),
]

SIMPLE_TAXONOMIES = {
    EvalCase.Taxonomy.TRANSLATION,
    EvalCase.Taxonomy.EXTRACTION,
    EvalCase.Taxonomy.EDITING,
    EvalCase.Taxonomy.RUSSIAN_STYLE,
}
COMPLEX_TAXONOMIES = {
    EvalCase.Taxonomy.DEBUGGING,
    EvalCase.Taxonomy.CODING,
    EvalCase.Taxonomy.RESEARCH,
    EvalCase.Taxonomy.REASONING,
    EvalCase.Taxonomy.LONG_DOCUMENTS,
    EvalCase.Taxonomy.SPREADSHEETS,
}


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
    content_tokens = estimate_text_tokens(content)
    long_context = content_tokens > 2000 or any(
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
    image_request = any(token in normalized for token in ("изображен", "фото", "картин", "скриншот"))
    needs_vision = image_request and has_visual_files
    needs_tools = any(
        token in normalized
        for token in (
            "найди акту",
            "проверь в интернете",
            "сегодня",
            "сейчас",
            "последние новости",
            "новости",
            "курс",
            "цена",
            "стоимость",
            "расписание",
            "закон",
            "тариф",
            "кто сейчас",
            "последняя версия",
        )
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
            "content_tokens": content_tokens,
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
    total = sum(estimate_text_tokens(item.content) + 4 for item in recent)
    if not recent or recent[0].role != "user" or recent[0].content != content:
        total += estimate_text_tokens(content) + 4
    return max(32, total + 16)


def _fits_context(model, input_tokens, output_tokens=OUTPUT_TOKENS):
    allowed_output = min(output_tokens, model.max_output_tokens)
    return input_tokens + allowed_output + CONTEXT_SAFETY_TOKENS <= model.context_window


def _health_score(provider):
    return {
        Provider.HealthState.HEALTHY: 1.0,
        Provider.HealthState.UNKNOWN: 0.70,
        Provider.HealthState.DEGRADED: 0.45,
        Provider.HealthState.OPEN: 0.20,
    }.get(provider.health_state, 0.0)


def _normalize_inverse(value, minimum, maximum):
    if maximum <= minimum:
        return 1.0
    return 1 - ((value - minimum) / (maximum - minimum))


def _active_policy():
    policy = RoutingPolicyVersion.objects.filter(active=True).first()
    if not policy:
        policy, _created = RoutingPolicyVersion.objects.get_or_create(
            version="router-v2",
            defaults={
                "active": True,
                "mode_weights": DEFAULT_WEIGHTS,
                "thresholds": DEFAULT_THRESHOLDS,
            },
        )
        if not policy.active:
            raise ValidationError("Активная политика AUTO Router не настроена")
    return policy


def _auto_tier(classification: TaskClassification) -> str:
    """Map task complexity to one of the three admin-managed model pools."""
    signals = classification.signals
    if (
        classification.taxonomy in COMPLEX_TAXONOMIES
        or signals.get("long_context")
        or signals.get("needs_vision")
        or signals.get("needs_tools")
        or int(signals.get("content_tokens") or 0) >= 1200
    ):
        return "maximum"
    if (
        classification.taxonomy in SIMPLE_TAXONOMIES
        and int(signals.get("content_tokens") or 0) < 700
        and not signals.get("has_project_files")
    ):
        return "economy"
    return "balanced"


def _tier_pool(thresholds: dict, tier: str):
    raw = (thresholds.get("tier_models") or {}).get(tier)
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(value).strip() for value in raw if str(value).strip()]
    return []


def _tier_configuration_present(thresholds: dict) -> bool:
    raw = thresholds.get("tier_models")
    return isinstance(raw, dict) and any(_tier_pool(thresholds, tier) for tier in DEFAULT_WEIGHTS)


def _route_row(model, classification, input_tokens, *, default_quality, unknown_latency):
    reasons = []
    if not str(model.upstream_model or "").strip():
        reasons.append("upstream_model_missing")
    missing = set(classification.required_capabilities) - _capabilities(model)
    if missing:
        reasons.append("missing_capabilities:" + ",".join(sorted(missing)))
    if not provider_available(model.provider):
        reasons.append("provider_unavailable")
    if not _fits_context(model, input_tokens):
        reasons.append("context_window_too_small")
    price_quote = None
    try:
        price = active_price(model.slug)
        price_quote = quote(
            price,
            input_tokens,
            min(OUTPUT_TOKENS, model.max_output_tokens),
            provider_slug=model.provider.slug,
            model_slug=model.slug,
        )
        charge = price_quote.user_charge_rub
        if not price_quote.margin_allowed:
            reasons.append("margin_below_floor")
        if not quote_has_procurement_capacity(model.provider, price_quote):
            reasons.append("procurement_balance_insufficient")
    except ValidationError:
        charge = None
        reasons.append("price_not_configured")
    quality, quality_source, eval_run = _quality(
        model, classification.taxonomy, default_quality
    )
    return {
        "model": model.slug,
        "provider": model.provider.slug,
        "model_version": model.current_version.version if model.current_version else None,
        "exact_api_id": model.upstream_model,
        "status": "rejected" if reasons else "eligible",
        "reasons": reasons,
        "quality": quality,
        "quality_source": quality_source,
        "eval_run": eval_run,
        "latency_ms": model.provider.last_latency_ms or unknown_latency,
        "health": model.provider.health_state,
        "context_window": model.context_window,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": min(OUTPUT_TOKENS, model.max_output_tokens),
        "estimated_cost_rub": str(charge) if charge is not None else None,
        "estimated_provider_cost_rub": str(price_quote.provider_cost_rub) if price_quote else None,
        "gross_margin_percent": str(price_quote.gross_margin_percent) if price_quote else None,
        "score": None,
    }


def _manual_route(policy, classification, conversation, input_tokens):
    try:
        primary = AIModel.objects.select_related(
            "provider", "fallback_model", "current_version"
        ).get(slug=conversation.selected_model, enabled=True)
    except AIModel.DoesNotExist as exc:
        raise ValidationError("Выбранная модель недоступна") from exc

    thresholds = policy.thresholds or {}
    default_quality = float(thresholds.get("default_quality", 0.55))
    unknown_latency = int(thresholds.get("unknown_latency_ms", 1500))
    multiplier = Decimal(str(thresholds.get("fallback_price_multiplier", 1.5)))

    # Primary first, then its explicit fallback chain, then every other routable
    # model as continuity protection. Duplicates are removed deterministically.
    ordered = []
    seen = set()
    current = primary
    while current and current.pk not in seen:
        seen.add(current.pk)
        ordered.append(current)
        current = current.fallback_model
        if current:
            current = AIModel.objects.select_related(
                "provider", "fallback_model", "current_version"
            ).filter(pk=current.pk, enabled=True).first()
    for model in AIModel.objects.filter(enabled=True).select_related(
        "provider", "current_version"
    ).order_by("provider__priority", "display_name"):
        if model.pk not in seen:
            ordered.append(model)
            seen.add(model.pk)

    rows = [
        _route_row(
            model,
            classification,
            input_tokens,
            default_quality=default_quality,
            unknown_latency=unknown_latency,
        )
        for model in ordered
    ]
    primary_row = rows[0]
    eligible_rows = [row for row in rows if row["status"] == "eligible"]
    if not eligible_rows:
        raise ValidationError("Нет доступной модели для выполнения запроса")

    baseline = None
    if primary_row.get("estimated_cost_rub") is not None:
        baseline = Decimal(primary_row["estimated_cost_rub"])
    if baseline is None:
        baseline = Decimal(eligible_rows[0]["estimated_cost_rub"])

    allowed = []
    for row in eligible_rows:
        charge = Decimal(row["estimated_cost_rub"])
        row["fallback_allowed"] = charge <= baseline * multiplier or row is eligible_rows[0]
        if row["fallback_allowed"]:
            row["rank"] = len(allowed) + 1
            allowed.append(row)
    if not allowed:
        raise ValidationError("Резервная модель превышает допустимый лимит стоимости")

    lookup = {model.slug: model for model in ordered}
    selected_row = allowed[0]
    selected = lookup[selected_row["model"]]
    if selected.pk == primary.pk:
        explanation = f"Используется выбранная модель {primary.display_name}."
    else:
        explanation = (
            f"Выбранная модель {primary.display_name} сейчас недоступна; "
            f"запрос автоматически направлен в {selected.display_name}."
        )
    return RouteSelection(
        policy=policy,
        classification=classification,
        selected=selected,
        ordered_models=[lookup[row["model"]] for row in allowed],
        candidates=rows,
        explanation=explanation,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=min(OUTPUT_TOKENS, selected.max_output_tokens),
        estimated_cost_rub=Decimal(selected_row["estimated_cost_rub"]),
    )


def select_route(*, conversation, content):
    policy = _active_policy()
    classification = classify_task(content, conversation)
    input_tokens = _estimated_input(conversation, content)
    requested_mode = conversation.routing_mode
    if requested_mode == "manual":
        return _manual_route(policy, classification, conversation, input_tokens)

    effective_tier = _auto_tier(classification) if requested_mode == "auto" else requested_mode
    weights = (policy.mode_weights or {}).get(effective_tier) or DEFAULT_WEIGHTS.get(effective_tier)
    if not weights:
        raise ValidationError("Неизвестный режим маршрутизации")

    thresholds = policy.thresholds or {}
    default_quality = float(thresholds.get("default_quality", 0.55))
    economy_min = float(thresholds.get("economy_min_quality", 0.60))
    unknown_latency = int(thresholds.get("unknown_latency_ms", 1500))
    pool = _tier_pool(thresholds, effective_tier)
    configured_pools = _tier_configuration_present(thresholds)
    if configured_pools and not pool:
        raise ValidationError(
            f"Администратор не настроил модели для уровня «{MODE_LABELS[effective_tier]}»"
        )

    queryset = AIModel.objects.filter(enabled=True).select_related("provider", "current_version")
    if pool:
        queryset = queryset.filter(slug__in=pool)

    candidates = []
    model_lookup = {}
    for model in queryset:
        model_lookup[model.slug] = model
        item = _route_row(
            model,
            classification,
            input_tokens,
            default_quality=default_quality,
            unknown_latency=unknown_latency,
        )
        if (
            effective_tier == "economy"
            and item["quality_source"] == "eval"
            and item["quality"] < economy_min
        ):
            item["reasons"].append("quality_below_economy_minimum")
            item["status"] = "rejected"
        item["admin_tier"] = effective_tier
        candidates.append(item)

    eligible = [item for item in candidates if item["status"] == "eligible"]
    if not eligible:
        label = MODE_LABELS.get(effective_tier, effective_tier)
        raise ValidationError(f"В уровне «{label}» сейчас нет доступных моделей")

    costs = [float(item["estimated_cost_rub"]) for item in eligible]
    latencies = [item["latency_ms"] for item in eligible]
    contexts = [math.log2(item["context_window"]) for item in eligible]
    for item in eligible:
        cost_score = _normalize_inverse(
            float(item["estimated_cost_rub"]), min(costs), max(costs)
        )
        latency_score = _normalize_inverse(
            item["latency_ms"], min(latencies), max(latencies)
        )
        context_score = (
            (math.log2(item["context_window"]) - min(contexts))
            / (max(contexts) - min(contexts))
            if max(contexts) > min(contexts)
            else 1.0
        )
        model = model_lookup[item["model"]]
        health_score = _health_score(model.provider)
        tag_bonus = 0.05 if classification.taxonomy in model.routing_tags else 0
        needs_bonus = (
            0.03 if classification.signals["needs_tools"] and "tools" in _capabilities(model) else 0
        )
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

    # Admin pool order is authoritative as a soft priority. Within the same pool
    # position score decides, preserving quality/cost/latency optimization.
    pool_rank = {slug: index for index, slug in enumerate(pool)} if pool else {}
    eligible.sort(
        key=lambda item: (
            pool_rank.get(item["model"], 10_000),
            -item["score"],
            item["model"],
        )
        if pool
        else (-item["score"], item["model"])
    )
    selected_item = eligible[0]
    selected = model_lookup[selected_item["model"]]
    selected_cost = Decimal(selected_item["estimated_cost_rub"])
    multiplier = Decimal(str(thresholds.get("fallback_price_multiplier", 1.5)))

    ordered_models = []
    for rank, item in enumerate(eligible, 1):
        item["rank"] = rank
        allowed = rank == 1 or Decimal(item["estimated_cost_rub"]) <= selected_cost * multiplier
        item["fallback_allowed"] = allowed
        if allowed:
            ordered_models.append(model_lookup[item["model"]])

    task_label = TASK_LABELS.get(classification.taxonomy, classification.taxonomy)
    tier_label = MODE_LABELS[effective_tier]
    if requested_mode == "auto":
        explanation = (
            f"AUTO определил сложность «{tier_label}» для задачи «{task_label}» и выбрал "
            f"{selected.display_name}; при сбое будет использована следующая доступная модель этого уровня."
        )
    else:
        explanation = (
            f"Уровень «{tier_label}»: выбрана {selected.display_name}; "
            "при сбое будет использована следующая доступная модель этого уровня."
        )

    return RouteSelection(
        policy=policy,
        classification=classification,
        selected=selected,
        ordered_models=ordered_models,
        candidates=candidates,
        explanation=explanation,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=min(OUTPUT_TOKENS, selected.max_output_tokens),
        estimated_cost_rub=selected_cost,
    )
