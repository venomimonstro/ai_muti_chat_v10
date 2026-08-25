from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.files.models import FileAsset
from apps.files.rag import prepare_chunk


class Command(BaseCommand):
    help = "Rebuild embeddings, ACL metadata and injection flags for file chunks"

    def add_arguments(self, parser):
        parser.add_argument("--file", dest="file_id")
        parser.add_argument("--project", dest="project_id")

    def handle(self, *_args, **options):
        if bool(options["file_id"]) == bool(options["project_id"]):
            raise CommandError("Укажите ровно один параметр: --file или --project")
        assets = FileAsset.objects.filter(deleted_at__isnull=True).prefetch_related("chunks")
        if options["file_id"]:
            assets = assets.filter(pk=options["file_id"])
        else:
            assets = assets.filter(project_id=options["project_id"])
        updated = 0
        with transaction.atomic():
            for asset in assets:
                for chunk in asset.chunks.all():
                    prepare_chunk(chunk, asset)
                    chunk.save(
                        update_fields=[
                            "content_sha256",
                            "embedding",
                            "embedding_model",
                            "acl_owner_id",
                            "acl_project_id",
                            "injection_risk",
                            "injection_signals",
                            "indexed_at",
                        ]
                    )
                    updated += 1
        self.stdout.write(self.style.SUCCESS(f"Переиндексировано фрагментов: {updated}"))
