from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import AIModel, Provider
from apps.billing.models import BalanceReservation, PriceVersion
from apps.billing.services import credit

from .branches import ensure_active_branch, fork_branch, visible_messages
from .compare import branch_from_variant, run_compare, synthesize_compare
from .models import CompareRun, Conversation, Message


def compare_registry():
    provider = Provider.objects.create(slug="compare-echo", name="Compare Echo")
    models = []
    for index in range(2):
        model = AIModel.objects.create(
            provider=provider,
            slug=f"compare-{index}",
            display_name=f"Compare {index}",
            upstream_model=f"echo-{index}",
            capabilities=["text", "streaming"],
        )
        PriceVersion.objects.create(
            model_slug=model.slug,
            input_rub_per_million=Decimal("10"),
            output_rub_per_million=Decimal("20"),
            markup_percent=Decimal("100"),
            effective_from=timezone.now(),
        )
        models.append(model)
    return models


@pytest.mark.django_db(transaction=True)
def test_compare_runs_models_and_settles_one_hard_reservation(settings):
    settings.COMPARE_CONFIRM_THRESHOLD_RUB = "999"
    user = User.objects.create_user(
        username="compare", email="compare@example.com", password="password123"
    )
    credit(user, Decimal("10"), "test", "compare")
    conversation = Conversation.objects.create(owner=user)
    models = compare_registry()

    run = run_compare(
        user=user,
        conversation=conversation,
        prompt="Сравни два варианта стратегии",
        model_slugs=[item.slug for item in models],
        idempotency_key="compare:test:one",
    )

    assert run.state == CompareRun.State.COMPLETED
    assert run.variants.count() == 2
    assert all(item.output.startswith("Тестовый ответ") for item in run.variants.all())
    assert run.actual_cost_rub > 0
    reservation = BalanceReservation.objects.get(pk=run.reservation_id)
    assert reservation.state == BalanceReservation.State.SETTLED
    assert reservation.actual_rub == run.actual_cost_rub
    replay = run_compare(
        user=user,
        conversation=conversation,
        prompt="Этот текст не должен создать новый запуск",
        model_slugs=[item.slug for item in models],
        idempotency_key="compare:test:one",
    )
    assert replay.id == run.id
    assert CompareRun.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_synthesis_and_compare_variant_create_navigable_branch(settings):
    settings.COMPARE_CONFIRM_THRESHOLD_RUB = "999"
    user = User.objects.create_user(
        username="compare-branch", email="compare-branch@example.com", password="password123"
    )
    credit(user, Decimal("10"), "test", "compare-branch")
    conversation = Conversation.objects.create(owner=user)
    branch = ensure_active_branch(conversation, user)
    source = Message.objects.create(
        conversation=conversation,
        branch=branch,
        role=Message.Role.USER,
        content="Исходный запрос",
    )
    models = compare_registry()
    run = run_compare(
        user=user,
        conversation=conversation,
        source_message=source,
        prompt=source.content,
        model_slugs=[item.slug for item in models],
        idempotency_key="compare:test:branch",
    )

    synthesize_compare(user=user, compare_run=run, model_slug=models[0].slug, confirmed=True)
    run.refresh_from_db()
    assert run.synthesis_output.startswith("Тестовый ответ")
    variant = run.variants.first()
    created = branch_from_variant(user=user, variant=variant)
    conversation.refresh_from_db()
    assert conversation.active_branch_id == created.id
    assert created.parent_id == branch.id
    assert visible_messages(conversation).filter(content=variant.output).exists()


@pytest.mark.django_db
def test_branch_fork_inherits_only_history_through_source():
    user = User.objects.create_user(
        username="branches", email="branches@example.com", password="password123"
    )
    conversation = Conversation.objects.create(owner=user)
    root = ensure_active_branch(conversation, user)
    first = Message.objects.create(
        conversation=conversation, branch=root, role=Message.Role.USER, content="Первое"
    )
    Message.objects.create(
        conversation=conversation, branch=root, role=Message.Role.ASSISTANT, content="Позднее"
    )

    child = fork_branch(
        conversation=conversation, user=user, source_message=first, title="Новая ветка"
    )

    assert child.inherited_message_ids == [str(first.id)]
    assert list(visible_messages(conversation).values_list("content", flat=True)) == ["Первое"]


@pytest.mark.django_db
def test_compare_preview_api_requires_two_models(settings):
    settings.COMPARE_CONFIRM_THRESHOLD_RUB = "999"
    user = User.objects.create_user(
        username="compare-api", email="compare-api@example.com", password="password123"
    )
    conversation = Conversation.objects.create(owner=user)
    model = compare_registry()[0]
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        f"/api/v1/conversations/{conversation.id}/compare/preview/",
        {"prompt": "Запрос", "models": [model.slug]},
        format="json",
    )
    assert response.status_code == 400
