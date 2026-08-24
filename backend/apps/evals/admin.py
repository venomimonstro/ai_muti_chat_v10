from django.contrib import admin

from .models import EvalCase, EvalResult, EvalRun, ModelScore


class ImmutableAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in {"GET", "HEAD", "OPTIONS"}

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EvalCase)
class EvalCaseAdmin(admin.ModelAdmin):
    list_display = ("slug", "dataset_version", "taxonomy", "min_score", "enabled")
    list_filter = ("dataset_version", "taxonomy", "enabled")
    search_fields = ("slug", "title", "prompt")


@admin.register(EvalRun)
class EvalRunAdmin(ImmutableAdmin):
    list_display = (
        "model",
        "dataset_version",
        "state",
        "gate_status",
        "average_score",
        "started_at",
    )
    list_filter = ("state", "gate_status", "dataset_version")
    readonly_fields = [field.name for field in EvalRun._meta.fields]


@admin.register(EvalResult)
class EvalResultAdmin(ImmutableAdmin):
    list_display = ("run", "case", "score", "passed", "latency_ms", "cost_rub")
    list_filter = ("passed", "hallucinated", "case__taxonomy")
    readonly_fields = [field.name for field in EvalResult._meta.fields]


@admin.register(ModelScore)
class ModelScoreAdmin(ImmutableAdmin):
    list_display = ("model", "taxonomy", "score", "eligible_for_promotion", "created_at")
    list_filter = ("taxonomy", "eligible_for_promotion")
    readonly_fields = [field.name for field in ModelScore._meta.fields]
