from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.adapters import ProviderError, ProviderResult
from apps.ai_registry.models import AIModel, Provider
from apps.ai_registry.versioning import create_model_version
from apps.billing.models import PriceVersion

from .judge import score_response
from .models import EvalCase, EvalResult, EvalRun, ModelScore
from .services import run_evaluation


def create_model(slug="eval-model"):
    provider = Provider.objects.create(slug=f"{slug}-provider", name="Eval Provider")
    model = AIModel.objects.create(
        provider=provider,
        slug=slug,
        display_name="Eval Model",
        upstream_model=slug,
    )
    PriceVersion.objects.create(
        model_slug=slug,
        input_rub_per_million=Decimal("10"),
        output_rub_per_million=Decimal("20"),
        markup_percent=Decimal("100"),
        effective_from=timezone.now(),
    )
    return model


def create_cases(dataset="test-v1"):
    return [
        EvalCase.objects.create(
            dataset_version=dataset,
            slug="reasoning",
            taxonomy=EvalCase.Taxonomy.REASONING,
            title="Reasoning",
            prompt="Сколько будет 10 + 7?",
            rubric={"exact_answer": "17", "russian_required": False, "max_chars": 4},
            min_score=Decimal("0.9"),
        ),
        EvalCase.objects.create(
            dataset_version=dataset,
            slug="spreadsheet",
            taxonomy=EvalCase.Taxonomy.SPREADSHEETS,
            title="Spreadsheet",
            prompt="Формула суммы B2:B10",
            rubric={
                "exact_answer": "=SUM(B2:B10)",
                "russian_required": False,
                "max_chars": 20,
            },
            min_score=Decimal("0.9"),
        ),
    ]


class SequenceAdapter:
    def __init__(self, responses):
        self.responses = iter(responses)

    def generate(self, **_kwargs):
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return ProviderResult(
            text=value,
            input_tokens=10,
            output_tokens=2,
            provider_request_id="eval:test",
        )


class RecordingAdapter(SequenceAdapter):
    def __init__(self, responses):
        super().__init__(responses)
        self.models = []

    def generate(self, **kwargs):
        self.models.append(kwargs["model"])
        return super().generate(**kwargs)


def test_deterministic_judge_penalizes_hallucination_and_verbosity():
    clean = score_response(
        "Вода кипит при 100 градусах.",
        {
            "required_phrases": ["100 градус"],
            "forbidden_phrases": ["90 градус"],
            "max_chars": 80,
        },
    )
    bad = score_response(
        "Вода кипит при 90 градусах. " * 10,
        {
            "required_phrases": ["100 градус"],
            "forbidden_phrases": ["90 градус"],
            "max_chars": 80,
        },
    )
    assert clean.correctness == 1
    assert clean.hallucinated is False
    assert bad.hallucinated is True
    assert bad.verbosity_control < 1
    assert bad.score < clean.score


@pytest.mark.django_db
def test_bundled_dataset_covers_full_taxonomy_and_load_is_idempotent():
    call_command("load_eval_dataset", verbosity=0)
    call_command("load_eval_dataset", verbosity=0)
    cases = EvalCase.objects.filter(dataset_version="ru-core-v1", enabled=True)
    assert cases.count() == 15
    assert set(cases.values_list("taxonomy", flat=True)) == {
        value for value, _label in EvalCase.Taxonomy.choices
    }


@pytest.mark.django_db
def test_runner_collects_score_cost_latency_and_scorecards(settings):
    settings.EVAL_MIN_AVERAGE_SCORE = 0.9
    settings.EVAL_MAX_HALLUCINATION_RATE = 0
    settings.EVAL_MAX_ERROR_RATE = 0
    model = create_model()
    create_cases()

    run = run_evaluation(
        model=model,
        dataset_version="test-v1",
        adapter=SequenceAdapter(["17", "=SUM(B2:B10)"]),
    )

    assert run.state == EvalRun.State.COMPLETED
    assert run.gate_status == EvalRun.Gate.PASSED
    assert run.average_score == Decimal("1.0000")
    assert run.total_cost_rub == Decimal("0.0004")
    assert run.total_input_tokens == 20
    assert run.total_output_tokens == 4
    assert run.taxonomy_scorecard == {"reasoning": 1.0, "spreadsheets": 1.0}
    assert run.quality_scorecard["correctness"] == 1.0
    assert run.quality_scorecard["long_context_stability"] is None
    assert EvalResult.objects.filter(run=run, passed=True).count() == 2
    result = run.results.get(case__slug="reasoning")
    assert result.case_snapshot["prompt"] == "Сколько будет 10 + 7?"
    assert result.price_version_id_snapshot is not None
    assert run.model_snapshot["slug"] == model.slug
    assert ModelScore.objects.filter(run=run, eligible_for_promotion=True).count() == 2


@pytest.mark.django_db
def test_runner_evaluates_candidate_exact_id_without_promoting_it(settings):
    settings.EVAL_MIN_AVERAGE_SCORE = 0
    settings.EVAL_MAX_HALLUCINATION_RATE = 1
    settings.EVAL_MAX_ERROR_RATE = 0
    model = create_model("version-eval")
    create_cases("version-eval-v1")
    candidate = create_model_version(
        model=model,
        version="candidate-v2",
        exact_api_id="provider-exact-v2",
        max_output_tokens=512,
    )
    adapter = RecordingAdapter(["17", "=SUM(B2:B10)"])
    run = run_evaluation(
        model=model,
        model_version=candidate,
        dataset_version="version-eval-v1",
        adapter=adapter,
    )
    model.refresh_from_db()
    assert adapter.models == ["provider-exact-v2", "provider-exact-v2"]
    assert run.model_version_id_snapshot == candidate.id
    assert run.model_snapshot["exact_api_id"] == "provider-exact-v2"
    assert model.upstream_model == "version-eval"


@pytest.mark.django_db
def test_regression_gate_blocks_weaker_run(settings):
    settings.EVAL_MIN_AVERAGE_SCORE = 0.4
    settings.EVAL_MAX_HALLUCINATION_RATE = 1
    settings.EVAL_MAX_ERROR_RATE = 1
    settings.EVAL_MAX_REGRESSION = 0.05
    model = create_model("regression-model")
    create_cases("regression-v1")
    baseline = run_evaluation(
        model=model,
        dataset_version="regression-v1",
        adapter=SequenceAdapter(["17", "=SUM(B2:B10)"]),
    )
    candidate = run_evaluation(
        model=model,
        dataset_version="regression-v1",
        baseline=baseline,
        adapter=SequenceAdapter(["18", "=AVERAGE(B2:B10)"]),
    )
    assert baseline.gate_status == EvalRun.Gate.PASSED
    assert candidate.gate_status == EvalRun.Gate.FAILED
    assert "quality_regression" in candidate.gate_reasons
    assert "taxonomy_regression:reasoning" in candidate.gate_reasons
    assert not ModelScore.objects.filter(run=candidate, eligible_for_promotion=True).exists()


@pytest.mark.django_db
def test_case_failure_is_recorded_and_fails_gate(settings):
    settings.EVAL_MIN_AVERAGE_SCORE = 0
    settings.EVAL_MAX_HALLUCINATION_RATE = 1
    settings.EVAL_MAX_ERROR_RATE = 0
    model = create_model("failing-model")
    create_cases("errors-v1")
    run = run_evaluation(
        model=model,
        dataset_version="errors-v1",
        adapter=SequenceAdapter(
            [ProviderError("timeout", code="timeout"), "=SUM(B2:B10)"]
        ),
    )
    assert run.state == EvalRun.State.COMPLETED
    assert run.gate_status == EvalRun.Gate.FAILED
    assert run.error_count == 1
    assert run.results.get(case__slug="reasoning").error_code == "timeout"


@pytest.mark.django_db
def test_eval_api_is_staff_only():
    user = User.objects.create_user(
        username="eval-user", email="eval-user@example.com", password="password123"
    )
    staff = User.objects.create_user(
        username="eval-admin",
        email="eval-admin@example.com",
        password="password123",
        is_staff=True,
        role=User.Role.PLATFORM_ADMIN,
    )
    EvalCase.objects.create(
        dataset_version="api-v1",
        slug="api-case",
        taxonomy=EvalCase.Taxonomy.QA,
        title="API case",
        prompt="Вопрос",
        rubric={},
    )
    client = APIClient()
    client.force_authenticate(user)
    assert client.get("/api/v1/eval-cases/").status_code == 403
    client.force_authenticate(staff)
    response = client.get("/api/v1/eval-cases/?dataset=api-v1")
    assert response.status_code == 200
    assert response.data[0]["slug"] == "api-case"
