import math

from django.core.management.base import BaseCommand, CommandError

from apps.files.semantic_embeddings import MODEL_VERSION, embed_query


class Command(BaseCommand):
    help = "Load the configured semantic model and verify a finite 384-dimensional embedding"

    def handle(self, *args, **options):
        try:
            vector = embed_query("semantic readiness check")
        except Exception as exc:
            raise CommandError(f"Semantic model unavailable: {exc}") from exc
        if len(vector) != 384:
            raise CommandError(f"Semantic model returned {len(vector)} dimensions instead of 384")
        if not vector or not all(math.isfinite(float(item)) for item in vector):
            raise CommandError("Semantic model returned non-finite values")
        if not any(abs(float(item)) > 0 for item in vector):
            raise CommandError("Semantic model returned an all-zero vector")
        self.stdout.write(self.style.SUCCESS(f"Semantic model ready: {MODEL_VERSION}"))
