import uuid

from django.db import models


class EvalCase(models.Model):
    class Taxonomy(models.TextChoices):
        QA = "qa", "Бытовой Q&A"
        COPYWRITING = "copywriting", "Копирайтинг"
        EDITING = "editing", "Редактирование"
        SEO = "seo", "SEO"
        MARKETING = "marketing", "Маркетинговый анализ"
        CODING = "coding", "Программирование"
        DEBUGGING = "debugging", "Отладка"
        SPREADSHEETS = "spreadsheets", "Таблицы"
        LONG_DOCUMENTS = "long_documents", "Длинные документы"
        EXTRACTION = "extraction", "Извлечение фактов"
        REASONING = "reasoning", "Рассуждение"
        RESEARCH = "research", "Исследование"
        STRUCTURING = "structuring", "Структурирование"
        TRANSLATION = "translation", "Перевод"
        RUSSIAN_STYLE = "russian_style", "Русский язык и стилистика"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset_version = models.CharField(max_length=80, db_index=True)
    slug = models.SlugField(max_length=120)
    taxonomy = models.CharField(max_length=32, choices=Taxonomy.choices, db_index=True)
    title = models.CharField(max_length=200)
    prompt = models.TextField()
    system_prompt = models.TextField(blank=True)
    rubric = models.JSONField(default=dict)
    tags = models.JSONField(default=list, blank=True)
    min_score = models.DecimalField(max_digits=4, decimal_places=3, default="0.700")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["dataset_version", "taxonomy", "slug"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_version", "slug"], name="unique_eval_case_version_slug"
            )
        ]


class EvalRun(models.Model):
    class Kind(models.TextChoices):
        OFFLINE = "offline", "Offline"
        SHADOW = "shadow", "Shadow"
        AB = "ab", "A/B"

    class State(models.TextChoices):
        RUNNING = "running", "Выполняется"
        COMPLETED = "completed", "Завершён"
        FAILED = "failed", "Ошибка"

    class Gate(models.TextChoices):
        PENDING = "pending", "Ожидает"
        PASSED = "passed", "Пройден"
        FAILED = "failed", "Не пройден"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model = models.ForeignKey(
        "ai_registry.AIModel", on_delete=models.PROTECT, related_name="eval_runs"
    )
    dataset_version = models.CharField(max_length=80, db_index=True)
    baseline = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="comparisons"
    )
    run_kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.OFFLINE)
    model_snapshot = models.JSONField(default=dict)
    state = models.CharField(max_length=16, choices=State.choices, default=State.RUNNING)
    gate_status = models.CharField(max_length=16, choices=Gate.choices, default=Gate.PENDING)
    case_count = models.PositiveIntegerField(default=0)
    passed_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    average_score = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    hallucination_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    average_latency_ms = models.PositiveIntegerField(default=0)
    total_cost_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    total_input_tokens = models.PositiveIntegerField(default=0)
    total_output_tokens = models.PositiveIntegerField(default=0)
    taxonomy_scorecard = models.JSONField(default=dict, blank=True)
    quality_scorecard = models.JSONField(default=dict, blank=True)
    gate_reasons = models.JSONField(default=list, blank=True)
    runner_version = models.CharField(max_length=40, default="eval-v1")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]


class EvalResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(EvalRun, on_delete=models.CASCADE, related_name="results")
    case = models.ForeignKey(EvalCase, on_delete=models.PROTECT, related_name="results")
    response = models.TextField(blank=True)
    case_snapshot = models.JSONField(default=dict)
    score = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    correctness = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    instruction_following = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    russian_quality = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    verbosity_control = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    hallucinated = models.BooleanField(default=False)
    passed = models.BooleanField(default=False)
    latency_ms = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_rub = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    price_version_id_snapshot = models.UUIDField(null=True, blank=True)
    provider_request_id = models.CharField(max_length=200, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    judge_details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["case__taxonomy", "case__slug"]
        constraints = [
            models.UniqueConstraint(fields=["run", "case"], name="unique_eval_result_run_case")
        ]


class ModelScore(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(EvalRun, on_delete=models.CASCADE, related_name="model_scores")
    model = models.ForeignKey(
        "ai_registry.AIModel", on_delete=models.PROTECT, related_name="scorecards"
    )
    taxonomy = models.CharField(max_length=32, choices=EvalCase.Taxonomy.choices)
    score = models.DecimalField(max_digits=5, decimal_places=4)
    case_count = models.PositiveIntegerField()
    eligible_for_promotion = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "taxonomy"]
        constraints = [
            models.UniqueConstraint(fields=["run", "taxonomy"], name="unique_model_score_run_taxonomy")
        ]
