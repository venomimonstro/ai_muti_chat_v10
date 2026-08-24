from rest_framework import serializers

from .models import EvalCase, EvalResult, EvalRun, ModelScore


class EvalCaseSerializer(serializers.ModelSerializer):
    taxonomy_label = serializers.CharField(source="get_taxonomy_display", read_only=True)

    class Meta:
        model = EvalCase
        fields = (
            "id",
            "dataset_version",
            "slug",
            "taxonomy",
            "taxonomy_label",
            "title",
            "prompt",
            "system_prompt",
            "rubric",
            "tags",
            "min_score",
            "enabled",
            "updated_at",
        )


class EvalResultSerializer(serializers.ModelSerializer):
    case_slug = serializers.CharField(source="case.slug", read_only=True)
    taxonomy = serializers.CharField(source="case.taxonomy", read_only=True)

    class Meta:
        model = EvalResult
        fields = (
            "id",
            "case_slug",
            "taxonomy",
            "response",
            "case_snapshot",
            "score",
            "correctness",
            "instruction_following",
            "russian_quality",
            "verbosity_control",
            "hallucinated",
            "passed",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cost_rub",
            "price_version_id_snapshot",
            "provider_request_id",
            "error_code",
            "judge_details",
        )


class EvalRunSerializer(serializers.ModelSerializer):
    model = serializers.CharField(source="model.slug", read_only=True)
    results = EvalResultSerializer(many=True, read_only=True)

    class Meta:
        model = EvalRun
        fields = (
            "id",
            "model",
            "dataset_version",
            "baseline",
            "run_kind",
            "model_snapshot",
            "state",
            "gate_status",
            "gate_reasons",
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
            "runner_version",
            "started_at",
            "completed_at",
            "results",
        )


class EvalRunListSerializer(EvalRunSerializer):
    class Meta(EvalRunSerializer.Meta):
        fields = tuple(field for field in EvalRunSerializer.Meta.fields if field != "results")


class ModelScoreSerializer(serializers.ModelSerializer):
    model = serializers.CharField(source="model.slug", read_only=True)
    dataset_version = serializers.CharField(source="run.dataset_version", read_only=True)

    class Meta:
        model = ModelScore
        fields = (
            "id",
            "run",
            "model",
            "dataset_version",
            "taxonomy",
            "score",
            "case_count",
            "eligible_for_promotion",
            "created_at",
        )
