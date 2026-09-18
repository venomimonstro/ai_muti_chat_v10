from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


# Automatic deploy rollback only resets application code. The forward database schema therefore
# has to remain compatible with the previous application version. Unknown data/schema rewrites
# are intentionally blocked from the automatic path and must use an explicit expand/contract plan.
ROLLBACK_INCOMPATIBLE = {
    "DeleteModel",
    "RemoveField",
    "RenameField",
    "RenameModel",
    "AlterField",
    "AlterModelTable",
    "RunSQL",
    "RunPython",
    "SeparateDatabaseAndState",
    "AddConstraint",
    "RemoveConstraint",
    "AlterUniqueTogether",
    "AlterIndexTogether",
}


def _operation_blocker(operation):
    name = operation.__class__.__name__
    if name in ROLLBACK_INCOMPATIBLE:
        return name
    if name == "AddField":
        field = operation.field
        # Adding a required column without a migration-time default is not safe on populated
        # tables and can leave the deploy half-applied. Nullable/defaulted expansion is allowed.
        if not field.null and not field.has_default() and not getattr(field, "primary_key", False):
            return "AddField(required_without_default)"
    return None


class Command(BaseCommand):
    help = "Block migrations that are unsafe for automatic application-code rollback"

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
                reason = _operation_blocker(operation)
                if reason:
                    blocked.append(f"{migration.app_label}.{migration.name}:{reason}")
        if blocked:
            for item in blocked:
                self.stdout.write(self.style.ERROR(f"BLOCK {item}"))
            raise CommandError(
                "Rollback-incompatible migration detected. Use a reviewed expand/contract release instead of automatic deploy/rollback."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Migration plan is compatible with application-code rollback; pending={len(plan)}"
            )
        )
