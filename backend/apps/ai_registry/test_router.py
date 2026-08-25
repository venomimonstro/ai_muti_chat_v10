import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.billing.models import MarkupRuleVersion, PriceVersion
from apps.billing.services import credit
from apps.chat.models import Conversation, RoutingDecision
from apps.chat.streaming import prepare
from apps.evals.models import EvalRun, ModelScore
from apps.files.models import FileAsset
from apps.projects.models import Project

from .models import AIModel, Provider
from .router import classify_task, select_route


def create_routable_model(
    slug,
    *,
    input_price,
    output_price,
    latency,
    capabilities=None,
    fallback_model=None,
):
    provider = Provider.objects.create(
        slug=f"{slug}-provider",
        name=slug,
        health_state=Provider.HealthState.HEALTHY,
        last_latency_ms=latency,
    )
    model = AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name=slug.title(),
        upstream_model=slug,
        capabilities=capabilities or ["text", "streaming"],
        fallback_model=fallback_model,
    )
    PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=Decimal(str(input_price)),
        output_rub_per_million=Decimal(str(output_price)),
        markup_percent=0,
        effective_from=timezone.now(),
    )
    return model


def add_eval_score(model, taxonomy, score):
    run = EvalRun.objects.create(
        model=model,
        dataset_version="router-test-v1",
        state=EvalRun.State.COMPLETED,
        gate_status=EvalRun.Gate.PASSED,
        average_score=score,
        completed_at=timezone.now(),
    )
    ModelScore.objects.create(
        run=run,
        model=model,
        taxonomy=taxonomy,
        score=score,
        case_count=1,
        eligible_for_promotion=True,
    )


@pytest.mark.django_db
def test_classifier_uses_free_heuristics_and_detects_vision():
    user = User.objects.create_user(
        username="classifier", email="classifier@example.com", password="password123"
    )
    project = Project.objects.create(owner=user, name="Vision")
    FileAsset.objects.create(
        owner=user,
        project=project,
        blob="router/image.png",
        original_name="screen.png",
        detected_type="png",
        size_bytes=10,
        sha256="c" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="router-vision",
    )
    conversation = Conversation.objects.create(owner=user, project=project)
    classification = classify_task("Исправь ошибку в Python на этом скриншоте", conversation)
    assert classification.taxonomy == "debugging"
    assert classification.required_capabilities == ["text", "vision"]
    assert classification.signals["has_project_files"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("mode", "expected"),
    [("economy", "cheap"), ("balanced", "strong"), ("maximum", "strong")],
)
def test_auto_modes_apply_different_quality_cost_latency_weights(mode, expected):
    user = User.objects.create_user(
        username=f"router-{mode}", email=f"router-{mode}@example.com", password="password123"
    )
    cheap = create_routable_model(
        "cheap", input_price=1, output_price=1, latency=1000
    )
    strong = create_routable_model(
        "strong", input_price=100, output_price=100, latency=100
    )
    add_eval_score(cheap, "marketing", Decimal("0.65"))
    add_eval_score(strong, "marketing", Decimal("0.95"))
    conversation = Conversation.objects.create(owner=user, routing_mode=mode)
    route = select_route(
        conversation=conversation,
        content="Проведи маркетинговый анализ рекламной воронки и конверсии",
    )
    assert route.selected.slug == expected
    assert route.candidates
    assert route.explanation.startswith("AUTO определил задачу")
    assert route.candidates[0].get("score") is not None


@pytest.mark.django_db
def test_capability_and_health_filters_are_hard_constraints():
    user = User.objects.create_user(
        username="router-filter", email="router-filter@example.com", password="password123"
    )
    project = Project.objects.create(owner=user, name="Files")
    FileAsset.objects.create(
        owner=user,
        project=project,
        blob="router/photo.jpg",
        original_name="photo.jpg",
        detected_type="jpeg",
        size_bytes=10,
        sha256="d" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="router-photo",
    )
    text_model = create_routable_model(
        "text-only", input_price=1, output_price=1, latency=10
    )
    vision_model = create_routable_model(
        "vision", input_price=10, output_price=10, latency=100, capabilities=["text", "vision"]
    )
    unavailable = create_routable_model(
        "vision-down", input_price=1, output_price=1, latency=1, capabilities=["text", "vision"]
    )
    unavailable.provider.emergency_disabled = True
    unavailable.provider.save(update_fields=["emergency_disabled"])
    conversation = Conversation.objects.create(
        owner=user, project=project, routing_mode=Conversation.RoutingMode.BALANCED
    )
    route = select_route(conversation=conversation, content="Проанализируй это фото")
    rejected = next(item for item in route.candidates if item["model"] == text_model.slug)
    health_rejected = next(item for item in route.candidates if item["model"] == unavailable.slug)
    assert route.selected == vision_model
    assert "missing_capabilities:vision" in rejected["reasons"]
    assert "provider_unavailable" in health_rejected["reasons"]


@pytest.mark.django_db
def test_auto_router_rejects_candidate_below_margin_floor():
    user = User.objects.create_user(
        username="router-margin", email="router-margin@example.com", password="password123"
    )
    unsafe = create_routable_model(
        "unsafe-margin", input_price=1, output_price=1, latency=1
    )
    safe = create_routable_model(
        "safe-margin", input_price=10, output_price=10, latency=100
    )
    MarkupRuleVersion.objects.create(
        scope_type=MarkupRuleVersion.Scope.MODEL,
        scope_key=unsafe.slug,
        markup_percent=0,
        effective_from=timezone.now(),
    )
    conversation = Conversation.objects.create(
        owner=user, routing_mode=Conversation.RoutingMode.BALANCED
    )
    route = select_route(conversation=conversation, content="Обычный вопрос")
    rejected = next(item for item in route.candidates if item["model"] == unsafe.slug)
    assert route.selected == safe
    assert "margin_below_floor" in rejected["reasons"]


@pytest.mark.django_db
def test_manual_fallback_cannot_silently_raise_price():
    user = User.objects.create_user(
        username="router-manual", email="router-manual@example.com", password="password123"
    )
    expensive = create_routable_model(
        "expensive-fallback", input_price=100, output_price=100, latency=100
    )
    primary = create_routable_model(
        "manual-primary",
        input_price=1,
        output_price=1,
        latency=100,
        fallback_model=expensive,
    )
    conversation = Conversation.objects.create(
        owner=user,
        selected_model=primary.slug,
        routing_mode=Conversation.RoutingMode.MANUAL,
    )
    route = select_route(conversation=conversation, content="Обычный вопрос")
    assert [model.slug for model in route.ordered_models] == [primary.slug]
    fallback = next(item for item in route.candidates if item["model"] == expensive.slug)
    assert fallback["status"] == "rejected"
    assert fallback["reasons"] == ["fallback_price_requires_consent"]


@pytest.mark.django_db(transaction=True)
def test_prepare_persists_explainable_routing_snapshot():
    user = User.objects.create_user(
        username="router-prepare", email="router-prepare@example.com", password="password123"
    )
    credit(user, 10, "test", "router")
    model = create_routable_model("router-selected", input_price=10, output_price=20, latency=50)
    conversation = Conversation.objects.create(
        owner=user, routing_mode=Conversation.RoutingMode.BALANCED
    )
    generation, created = prepare(
        user=user,
        conversation=conversation,
        content="Напиши рекламный оффер",
        client_message_id=uuid.uuid4(),
        idempotency_key="router:prepare",
    )
    decision = RoutingDecision.objects.get(generation=generation)
    assert created is True
    assert generation.model == model.slug
    assert decision.selected_model == model
    assert decision.task_taxonomy == "copywriting"
    assert generation.context_snapshot["routing"]["policy_version"] == "router-v1"
    assert generation.context_snapshot["routing"]["explanation"] == decision.explanation
