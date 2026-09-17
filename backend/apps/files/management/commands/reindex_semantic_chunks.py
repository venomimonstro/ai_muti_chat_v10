from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.files.models import FileChunk
from apps.files.rag import detect_prompt_injection
from apps.files.semantic_embeddings import MODEL_VERSION, embed_passage


class Command(BaseCommand):
    help = "Rebuild file chunk vectors using the current local multilingual semantic model"

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=64)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        batch_size = max(1, min(options["batch_size"], 256))
        queryset = FileChunk.objects.select_related("file").order_by("id")
        if not options["force"]:
            queryset = queryset.exclude(embedding_model=MODEL_VERSION)
        updated = 0
        batch = []
        for chunk in queryset.iterator(chunk_size=batch_size):
            chunk.embedding = embed_passage(chunk.content)
            chunk.embedding_model = MODEL_VERSION
            chunk.injection_risk, chunk.injection_signals = detect_prompt_injection(chunk.content)
            chunk.indexed_at = timezone.now()
            batch.append(chunk)
            if len(batch) >= batch_size:
                self._flush(batch)
                updated += len(batch)
                batch = []
                self.stdout.write(f"reindexed={updated}")
        if batch:
            self._flush(batch)
            updated += len(batch)
        self.stdout.write(self.style.SUCCESS(f"Semantic reindex complete: {updated} chunks"))

    @transaction.atomic
    def _flush(self, batch):
        FileChunk.objects.bulk_update(
            batch,
            ["embedding", "embedding_model", "injection_risk", "injection_signals", "indexed_at"],
            batch_size=len(batch),
        )
