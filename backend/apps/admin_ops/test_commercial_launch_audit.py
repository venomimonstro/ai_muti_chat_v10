import io
import json
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_commercial_launch_audit_runs_all_required_gates():
    seen = []

    def fake_call(name, *args, **kwargs):
        seen.append((name, kwargs))

    with patch(
        "apps.admin_ops.management.commands.commercial_launch_audit.call_command",
        side_effect=fake_call,
    ):
        call_command("commercial_launch_audit")

    assert [name for name, _kwargs in seen] == [
        "check",
        "commercial_config_check",
        "payment_commercial_check",
        "verify_financial_invariants",
        "prelaunch_check",
    ]
    assert seen[1][1]["require_healthy"] is True
    assert seen[2][1]["require_reconciliation"] is True
    assert seen[4][1]["strict"] is True


@pytest.mark.django_db
def test_commercial_launch_audit_blocks_when_nested_gate_fails():
    def fake_call(name, *args, **kwargs):
        if name == "verify_financial_invariants":
            raise CommandError("financial invariant failed")

    with patch(
        "apps.admin_ops.management.commands.commercial_launch_audit.call_command",
        side_effect=fake_call,
    ):
        with pytest.raises(CommandError, match="Commercial launch is BLOCKED"):
            call_command("commercial_launch_audit")


@pytest.mark.django_db
def test_commercial_launch_audit_json_is_single_valid_document():
    output = io.StringIO()

    def fake_call(name, *args, **kwargs):
        stdout = kwargs.get("stdout")
        if stdout:
            stdout.write(f"{name}: ok\n")

    with patch(
        "apps.admin_ops.management.commands.commercial_launch_audit.call_command",
        side_effect=fake_call,
    ):
        call_command("commercial_launch_audit", "--json", stdout=output)

    payload = json.loads(output.getvalue())
    assert payload["passed"] is True
    assert len(payload["gates"]) == 5


@pytest.mark.django_db
def test_commercial_launch_audit_json_survives_blocked_gate():
    output = io.StringIO()

    def fake_call(name, *args, **kwargs):
        if name == "payment_commercial_check":
            raise CommandError("payments blocked")
        stdout = kwargs.get("stdout")
        if stdout:
            stdout.write(f"{name}: ok\n")

    with patch(
        "apps.admin_ops.management.commands.commercial_launch_audit.call_command",
        side_effect=fake_call,
    ):
        with pytest.raises(CommandError):
            call_command("commercial_launch_audit", "--json", stdout=output)

    payload = json.loads(output.getvalue())
    assert payload["passed"] is False
    failed = [item for item in payload["gates"] if not item["passed"]]
    assert len(failed) == 1
    assert failed[0]["gate"] == "payment_commercial"
