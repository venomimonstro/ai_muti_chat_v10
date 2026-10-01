"""Keep Compare on the same customer-readiness contract as normal chat."""

from django.core.exceptions import ValidationError

from apps.ai_registry.reliability import model_client_ready


def install(compare_module) -> None:
    raw_models = compare_module._models
    raw_one_model = compare_module._one_model
    if getattr(raw_models, "_ai_workspace_client_readiness", False):
        return

    def _models(slugs):
        models = raw_models(slugs)
        for model in models:
            if not model_client_ready(model):
                raise ValidationError(f"Модель {model.slug} недоступна для Compare")
        return models

    def _one_model(slug):
        model = raw_one_model(slug)
        if not model_client_ready(model):
            raise ValidationError(f"Модель {model.slug} недоступна")
        return model

    _models._ai_workspace_client_readiness = True
    _models._raw_models = raw_models
    _one_model._ai_workspace_client_readiness = True
    _one_model._raw_one_model = raw_one_model
    compare_module._models = _models
    compare_module._one_model = _one_model
