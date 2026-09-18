from django.db import models
from django.db.migrations.operations.fields import AddField, AlterField
from django.db.migrations.operations.special import RunPython

from apps.admin_ops.management.commands.migration_safety_check import _operation_blocker


def test_migration_safety_allows_nullable_expand_field():
    operation = AddField(
        model_name="example",
        name="new_optional_value",
        field=models.CharField(max_length=50, null=True),
    )
    assert _operation_blocker(operation) is None


def test_migration_safety_blocks_required_expand_without_default():
    operation = AddField(
        model_name="example",
        name="new_required_value",
        field=models.CharField(max_length=50, null=False),
    )
    assert _operation_blocker(operation) == "AddField(required_without_default)"


def test_migration_safety_blocks_alter_field_and_data_rewrite():
    alter = AlterField(
        model_name="example",
        name="value",
        field=models.CharField(max_length=100),
    )
    rewrite = RunPython(code=lambda apps, schema_editor: None)
    assert _operation_blocker(alter) == "AlterField"
    assert _operation_blocker(rewrite) == "RunPython"
