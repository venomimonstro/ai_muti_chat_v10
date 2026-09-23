from apps.ai_registry.models import AIModel


SYSTEM_PROVIDER = "gigachat"


def _system_label(*values):
    text = " ".join(str(value or "") for value in values).casefold()
    if "max" in text:
        return "System Max"
    if "lite" in text:
        return "System Lite"
    return "System Pro"


def public_model_identity(model):
    if model.provider.slug == SYSTEM_PROVIDER:
        label = _system_label(model.slug, model.display_name, model.upstream_model)
        return {"model": label, "model_name": label, "provider": "system"}
    return {
        "model": model.slug,
        "model_name": model.display_name,
        "provider": model.provider.slug,
    }


def public_model_slug(slug):
    raw = str(slug or "")
    if not raw:
        return ""
    model = AIModel.objects.filter(slug=raw).select_related("provider").first()
    if model is not None:
        return public_model_identity(model)["model"]
    # Historical rows are normally protected by FK constraints, but this fallback
    # keeps old/exported data from exposing an internal provider if registry data
    # is ever repaired or imported separately.
    if raw.casefold().startswith("gigachat"):
        return _system_label(raw)
    return raw


def public_preview_rows(preview):
    rows = []
    for row in preview["models"]:
        identity = public_model_identity(row["model"])
        rows.append(
            {
                **identity,
                "expected_min_rub": str(row["minimum"].user_charge_rub),
                "expected_max_rub": str(row["maximum"].user_charge_rub),
            }
        )
    return rows


def serialize_compare_public(run):
    variants = list(run.variants.select_related("model__provider").all())
    variants.sort(key=lambda item: item.position)
    identities = [public_model_identity(item.model) for item in variants]
    return {
        "id": str(run.id),
        "conversation_id": str(run.conversation_id),
        "branch_id": str(run.branch_id) if run.branch_id else None,
        "source_message_id": str(run.source_message_id) if run.source_message_id else None,
        "prompt": run.prompt,
        "state": run.state,
        "models": [item["model"] for item in identities],
        "expected_min_rub": str(run.expected_min_rub),
        "expected_max_rub": str(run.expected_max_rub),
        "actual_cost_rub": str(run.actual_cost_rub),
        "synthesis_model": public_model_slug(run.synthesis_model_slug),
        "synthesis_output": run.synthesis_output,
        "synthesis_cost_rub": str(run.synthesis_cost_rub),
        "variants": [
            {
                "id": str(item.id),
                **identity,
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
            for item, identity in zip(variants, identities)
        ],
    }
