from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.models import AIModel, ModelVersion
from apps.ai_registry.versioning import rollback_model_version


class Command(BaseCommand):
    help = "Rollback production routing to a previously registered ModelVersion"

    def add_arguments(self, parser):
        parser.add_argument("--model", required=True)
        parser.add_argument("--version", required=True)
        parser.add_argument("--reason", required=True)

    def handle(self, *args, **options):
        try:
            model = AIModel.objects.get(slug=options["model"])
            target = ModelVersion.objects.get(model=model, version=options["version"])
            rollback_model_version(model=model, target=target, reason=options["reason"])
        except (AIModel.DoesNotExist, ModelVersion.DoesNotExist, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"rolled_back={model.slug}:{target.version}")
