import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.evals.models import EvalCase


class Command(BaseCommand):
    help = "Load or update a versioned eval dataset from JSON"

    def add_arguments(self, parser):
        default = Path(__file__).resolve().parents[2] / "data" / "ru_core_v1.json"
        parser.add_argument("path", nargs="?", default=str(default))

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.is_file():
            raise CommandError(f"Dataset not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        version = payload.get("dataset_version")
        cases = payload.get("cases")
        if not version or not isinstance(cases, list) or not cases:
            raise CommandError("Dataset must contain dataset_version and non-empty cases")
        valid_taxonomies = {value for value, _label in EvalCase.Taxonomy.choices}
        seen = set()
        for item in cases:
            slug = item.get("slug")
            taxonomy = item.get("taxonomy")
            if not slug or slug in seen or taxonomy not in valid_taxonomies:
                raise CommandError(f"Invalid or duplicate eval case: {slug}")
            if not isinstance(item.get("rubric"), dict):
                raise CommandError(f"Rubric must be an object: {slug}")
            seen.add(slug)
            EvalCase.objects.update_or_create(
                dataset_version=version,
                slug=slug,
                defaults={
                    "taxonomy": taxonomy,
                    "title": item["title"],
                    "prompt": item["prompt"],
                    "system_prompt": item.get("system_prompt", ""),
                    "rubric": item["rubric"],
                    "tags": item.get("tags", []),
                    "min_score": item.get("min_score", "0.700"),
                    "enabled": item.get("enabled", True),
                },
            )
        disabled = EvalCase.objects.filter(dataset_version=version).exclude(slug__in=seen).update(
            enabled=False
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {len(cases)} cases for {version}; disabled stale cases: {disabled}"
            )
        )
