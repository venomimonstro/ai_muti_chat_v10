from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.models import ModelVersion
from apps.ai_registry.versioning import promote_model_version
from apps.evals.models import EvalRun


class Command(BaseCommand):
    help = "Promote a candidate ModelVersion after a passing eval gate"

    def add_arguments(self, parser):
        parser.add_argument("--model", required=True)
        parser.add_argument("--version", required=True)
        parser.add_argument("--eval-run", required=True)
        parser.add_argument("--reason", default="")

    def handle(self, *args, **options):
        try:
            version = ModelVersion.objects.get(
                model__slug=options["model"], version=options["version"]
            )
            eval_run = EvalRun.objects.get(pk=options["eval_run"])
            promote_model_version(
                version=version, eval_run=eval_run, reason=options["reason"]
            )
        except (ModelVersion.DoesNotExist, EvalRun.DoesNotExist, ValidationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"promoted={version.model.slug}:{version.version}")
