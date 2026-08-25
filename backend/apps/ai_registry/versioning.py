from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.evals.models import EvalRun

from .models import AIModel, ModelVersion, ModelVersionTransition


def create_model_version(
    *,
    model: AIModel,
    version: str,
    exact_api_id: str,
    capabilities=None,
    routing_tags=None,
    context_window=None,
    max_output_tokens=None,
    release_notes="",
):
    """Register an immutable candidate without changing production routing."""
    return ModelVersion.objects.create(
        model=model,
        version=version,
        exact_api_id=exact_api_id,
        capabilities=list(model.capabilities if capabilities is None else capabilities),
        routing_tags=list(model.routing_tags if routing_tags is None else routing_tags),
        context_window=context_window or model.context_window,
        max_output_tokens=max_output_tokens or model.max_output_tokens,
        release_notes=release_notes,
    )


def _apply_version(model, version):
    model.current_version = version
    model.upstream_model = version.exact_api_id
    model.capabilities = version.capabilities
    model.routing_tags = version.routing_tags
    model.context_window = version.context_window
    model.max_output_tokens = version.max_output_tokens
    model.save(
        update_fields=[
            "current_version",
            "upstream_model",
            "capabilities",
            "routing_tags",
            "context_window",
            "max_output_tokens",
        ]
    )


def _validated_eval(version, eval_run):
    if eval_run.model_id != version.model_id:
        raise ValidationError("EvalRun принадлежит другой модели")
    if eval_run.state != EvalRun.State.COMPLETED or eval_run.gate_status != EvalRun.Gate.PASSED:
        raise ValidationError("Promotion требует завершённый EvalRun с пройденным gate")
    if eval_run.model_version_id_snapshot != version.id:
        raise ValidationError("EvalRun не проверял выбранную ModelVersion")
    if eval_run.model_snapshot.get("exact_api_id") != version.exact_api_id:
        raise ValidationError("Eval snapshot не совпадает с exact API id версии")


@transaction.atomic
def promote_model_version(*, version: ModelVersion, eval_run: EvalRun, reason=""):
    version = ModelVersion.objects.select_for_update().select_related("model").get(pk=version.pk)
    model = AIModel.objects.select_for_update().get(pk=version.model_id)
    _validated_eval(version, eval_run)
    if version.stage not in {ModelVersion.Stage.CANDIDATE, ModelVersion.Stage.CANARY}:
        raise ValidationError("Продвигать можно только candidate/canary версию")
    previous = model.current_version
    now = timezone.now()
    if previous:
        ModelVersion.objects.filter(pk=previous.pk).update(
            stage=ModelVersion.Stage.RETIRED, retired_at=now
        )
    ModelVersion.objects.filter(pk=version.pk).update(
        stage=ModelVersion.Stage.ACTIVE,
        eval_run_id=eval_run.id,
        activated_at=now,
        retired_at=None,
    )
    version.stage = ModelVersion.Stage.ACTIVE
    version.eval_run_id = eval_run.id
    version.activated_at = now
    version.retired_at = None
    _apply_version(model, version)
    ModelVersionTransition.objects.create(
        model=model,
        from_version=previous,
        to_version=version,
        action=ModelVersionTransition.Action.PROMOTE,
        eval_run_id=eval_run.id,
        reason=reason,
    )
    return version


@transaction.atomic
def rollback_model_version(*, model: AIModel, target: ModelVersion, reason: str):
    if not reason.strip():
        raise ValidationError("Для rollback обязательна причина")
    model = AIModel.objects.select_for_update().get(pk=model.pk)
    target = ModelVersion.objects.select_for_update().get(pk=target.pk)
    if target.model_id != model.id:
        raise ValidationError("ModelVersion принадлежит другой модели")
    previous = model.current_version
    if previous_id := model.current_version_id:
        if previous_id == target.id:
            raise ValidationError("Выбранная ModelVersion уже активна")
        ModelVersion.objects.filter(pk=previous_id).update(
            stage=ModelVersion.Stage.RETIRED, retired_at=timezone.now()
        )
    now = timezone.now()
    ModelVersion.objects.filter(pk=target.pk).update(
        stage=ModelVersion.Stage.ACTIVE, activated_at=now, retired_at=None
    )
    target.stage = ModelVersion.Stage.ACTIVE
    target.activated_at = now
    target.retired_at = None
    _apply_version(model, target)
    ModelVersionTransition.objects.create(
        model=model,
        from_version=previous,
        to_version=target,
        action=ModelVersionTransition.Action.ROLLBACK,
        reason=reason.strip(),
    )
    return target
