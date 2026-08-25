from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.models import AIModel, ModelVersion
from apps.evals.models import EvalRun
from apps.evals.services import run_evaluation


class Command(BaseCommand):
    help = "Run offline evals and apply quality regression gates"

    def add_arguments(self, parser):
        parser.add_argument("--model", required=True)
        parser.add_argument("--dataset", default="ru-core-v1")
        parser.add_argument("--baseline")
        parser.add_argument("--model-version")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--fail-on-regression", action="store_true")

    def handle(self, *args, **options):
        try:
            model = AIModel.objects.select_related("provider").get(
                slug=options["model"], enabled=True
            )
        except AIModel.DoesNotExist as exc:
            raise CommandError("Enabled model not found") from exc
        baseline = None
        model_version = None
        if options["model_version"]:
            try:
                model_version = ModelVersion.objects.get(
                    model=model, version=options["model_version"]
                )
            except ModelVersion.DoesNotExist as exc:
                raise CommandError("Model version not found") from exc
        if options["baseline"]:
            try:
                baseline = EvalRun.objects.get(pk=options["baseline"])
            except (EvalRun.DoesNotExist, ValueError) as exc:
                raise CommandError("Baseline run not found") from exc
        try:
            run = run_evaluation(
                model=model,
                dataset_version=options["dataset"],
                baseline=baseline,
                limit=options["limit"],
                model_version=model_version,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"run={run.id} model={model.slug} score={run.average_score} "
            f"gate={run.gate_status} latency_ms={run.average_latency_ms} "
            f"cost_rub={run.total_cost_rub}"
        )
        if options["fail_on_regression"] and run.gate_status != EvalRun.Gate.PASSED:
            raise CommandError("Eval regression gate failed: " + ", ".join(run.gate_reasons))
