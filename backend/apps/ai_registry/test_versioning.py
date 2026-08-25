from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.evals.models import EvalRun

from .models import AIModel, ModelVersion, ModelVersionTransition, Provider
from .versioning import create_model_version, promote_model_version, rollback_model_version


def registry_model():
    provider = Provider.objects.create(slug="registry-provider", name="Registry")
    model = AIModel.objects.create(
        provider=provider,
        slug="registry-model",
        display_name="Registry Model",
        upstream_model="model-old-exact",
        capabilities=["text"],
    )
    initial = ModelVersion.objects.create(
        model=model,
        version="2026-01-01",
        exact_api_id="model-old-exact",
        capabilities=["text"],
        stage=ModelVersion.Stage.ACTIVE,
        activated_at=timezone.now(),
    )
    model.current_version = initial
    model.save(update_fields=["current_version"])
    return model, initial


@pytest.mark.django_db(transaction=True)
def test_candidate_requires_exact_passing_eval_before_atomic_promotion():
    model, initial = registry_model()
    candidate = create_model_version(
        model=model,
        version="2026-08-25",
        exact_api_id="model-new-exact",
        capabilities=["text", "vision"],
        context_window=128_000,
        max_output_tokens=8192,
    )
    run = EvalRun.objects.create(
        model=model,
        dataset_version="registry-v1",
        state=EvalRun.State.COMPLETED,
        gate_status=EvalRun.Gate.PASSED,
        average_score=Decimal("0.9500"),
        model_version_id_snapshot=candidate.id,
        model_snapshot={"exact_api_id": candidate.exact_api_id},
        completed_at=timezone.now(),
    )
    promote_model_version(version=candidate, eval_run=run, reason="passed regression gate")
    model.refresh_from_db()
    initial.refresh_from_db()
    candidate.refresh_from_db()
    assert model.current_version == candidate
    assert model.upstream_model == "model-new-exact"
    assert model.capabilities == ["text", "vision"]
    assert candidate.stage == ModelVersion.Stage.ACTIVE
    assert initial.stage == ModelVersion.Stage.RETIRED
    assert ModelVersionTransition.objects.get().eval_run_id == run.id


@pytest.mark.django_db(transaction=True)
def test_promotion_rejects_failed_or_wrong_version_eval():
    model, _initial = registry_model()
    candidate = create_model_version(
        model=model, version="candidate", exact_api_id="model-candidate"
    )
    run = EvalRun.objects.create(
        model=model,
        dataset_version="registry-v1",
        state=EvalRun.State.COMPLETED,
        gate_status=EvalRun.Gate.FAILED,
        model_version_id_snapshot=candidate.id,
        model_snapshot={"exact_api_id": candidate.exact_api_id},
    )
    with pytest.raises(ValidationError):
        promote_model_version(version=candidate, eval_run=run)
    model.refresh_from_db()
    assert model.upstream_model == "model-old-exact"


@pytest.mark.django_db(transaction=True)
def test_rollback_restores_registered_exact_version_and_audits_reason():
    model, initial = registry_model()
    active = ModelVersion.objects.create(
        model=model,
        version="current",
        exact_api_id="model-current-exact",
        capabilities=["text", "tools"],
        stage=ModelVersion.Stage.CANDIDATE,
    )
    ModelVersion.objects.filter(pk=initial.pk).update(stage=ModelVersion.Stage.RETIRED)
    ModelVersion.objects.filter(pk=active.pk).update(stage=ModelVersion.Stage.ACTIVE)
    model.current_version = active
    model.upstream_model = active.exact_api_id
    model.save(update_fields=["current_version", "upstream_model"])
    rollback_model_version(model=model, target=initial, reason="provider regression")
    model.refresh_from_db()
    assert model.current_version == initial
    assert model.upstream_model == "model-old-exact"
    transition = ModelVersionTransition.objects.get()
    assert transition.action == ModelVersionTransition.Action.ROLLBACK
    assert transition.reason == "provider regression"


@pytest.mark.django_db
def test_registered_version_configuration_is_immutable():
    model, initial = registry_model()
    initial.exact_api_id = "silently-changed-alias"
    with pytest.raises(ValidationError):
        initial.save()
    model.refresh_from_db()
    assert model.upstream_model == "model-old-exact"
