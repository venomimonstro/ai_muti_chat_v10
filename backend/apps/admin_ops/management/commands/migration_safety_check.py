from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


DESTRUCTIVE = {
    "DeleteModel",
    "RemoveField",
    "RenameField",
    "RenameModel",
}


class Command(BaseCommand):
    help = "Block migrations that are unsafe for automatic application rollback"

    def handle(self, *args, **options):
        connection = connections["default"]
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        blocked = []
        for migration, backwards in plan:
            if backwards:
                continue
            for operation in migration.operations:
                name = operation.__class__.__name__
                if name in DESTRUCTIVE:
                    blocked.append(f"{migration.app_label}.{migration.name}:{name}")
                if name == "AlterField" and not getattr(operation, "preserve_default", True):
                    blocked.append(f"{migration.app_label}.{migration.name}:AlterField")
        if blocked:
            for item in blocked:
                self.stdout.write(self.style.ERROR(f"BLOCK {item}"))
            raise CommandError(
                "Destructive migration detected. Use expand/contract releases instead of automatic rollback."
            )
        self.stdout.write(self.style.SUCCESS(f"Migration plan is rollback-compatible; pending={len(plan)}"))
