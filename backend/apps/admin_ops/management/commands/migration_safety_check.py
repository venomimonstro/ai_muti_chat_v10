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
    "AlterModelTable",
    "RunSQL",
    "RunPython",
    "SeparateDatabaseAndState",
    "AddConstraint",
    "RemoveConstraint",
    "AlterUniqueTogether",
    "AlterIndexTogether",
}


def _choices_only_alter(old_field, new_field):
    if old_field is None:
        return False
    try:
        _old_name, old_path, old_args, old_kwargs = old_field.deconstruct()
        _new_name, new_path, new_args, new_kwargs = new_field.deconstruct()
    except Exception:
        return False
    old_kwargs = dict(old_kwargs)
    new_kwargs = dict(new_kwargs)
    old_kwargs.pop("choices", None)
    new_kwargs.pop("choices", None)
    return (
        old_path == new_path
        and old_args == new_args
        and old_kwargs == new_kwargs
    )


def _operation_blocker(operation, *, old_field=None):
    name = operation.__class__.__name__
    if name == "AlterField":
        if _choices_only_alter(old_field, operation.field):
            return None
        return "AlterField"
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
        # Build the exact state represented by already-applied migrations, then advance it in
        # lockstep with the pending plan. This lets us distinguish a metadata-only choices change
        # from a real schema-changing AlterField without maintaining a brittle allowlist.
        state = executor._create_project_state(with_applied_migrations=True)
        for migration, backwards in plan:
            if backwards:
                continue
            for operation in migration.operations:
                old_field = None
                if operation.__class__.__name__ == "AlterField":
                    model_state = state.models.get(
                        (migration.app_label, operation.model_name.lower())
                    )
                    if model_state is not None:
                        old_field = model_state.fields.get(operation.name)
                reason = _operation_blocker(operation, old_field=old_field)
                if reason:
                    blocked.append(f"{migration.app_label}.{migration.name}:{reason}")
            migration.mutate_state(state, preserve=False)
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
