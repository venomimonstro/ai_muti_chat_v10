import json

from django.core.management.base import BaseCommand, CommandError

from apps.ai_registry.models import AIModel
from apps.ai_registry.versioning import create_model_version


class Command(BaseCommand):
    help = "Register an immutable candidate ModelVersion without changing production routing"

    def add_arguments(self, parser):
        parser.add_argument("--model", required=True)
        parser.add_argument("--version", required=True)
        parser.add_argument("--exact-api-id", required=True)
        parser.add_argument("--capabilities")
        parser.add_argument("--routing-tags")
        parser.add_argument("--context-window", type=int)
        parser.add_argument("--max-output-tokens", type=int)
        parser.add_argument("--release-notes", default="")

    def handle(self, *args, **options):
        try:
            model = AIModel.objects.get(slug=options["model"])
            capabilities = (
                json.loads(options["capabilities"]) if options["capabilities"] else None
            )
            routing_tags = (
                json.loads(options["routing_tags"]) if options["routing_tags"] else None
            )
            version = create_model_version(
                model=model,
                version=options["version"],
                exact_api_id=options["exact_api_id"],
                capabilities=capabilities,
                routing_tags=routing_tags,
                context_window=options["context_window"],
                max_output_tokens=options["max_output_tokens"],
                release_notes=options["release_notes"],
            )
        except (AIModel.DoesNotExist, ValueError, TypeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"registered={version.model.slug}:{version.version} stage={version.stage}")
