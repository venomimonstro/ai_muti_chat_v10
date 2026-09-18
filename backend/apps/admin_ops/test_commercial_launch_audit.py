import io
import json
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

EXPECTED_GATES = [
    "check",
    "semantic_model_check",
    "commercial_config_check",
    "optional_features_check",
    "customer_trust_check",
    "payment_commercial_check",
    "economic_safety_check",
    "verify_financial_invariants",
    "operational_drills_check",
    "public_legal_check",
    "prelaunch_check",
]


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

    assert [name for name, _kwargs in seen] == EXPECTED_GATES
    by_name = {name: kwargs for name, kwargs in seen}
    assert by_name["check"]["deploy"] is True
    assert by_name["commercial_config_check"]["require_healthy"] is True
    assert by_name["payment_commercial_check"]["require_reconciliation"] is True
    assert by_name["prelaunch_check"]["strict"] is True


@pytest.mark.django_db
def test_commercial_launch_audit_blocks_when_economic_gate_fails():
    def fake_call(name, *args, **kwargs):
        if name == "economic_safety_check":
            raise CommandError("economic invariant failed")

    with patch(
        "apps.admin_ops.management.commands.commercial_launch_audit.call_command",
        side_effect=fake_call,
    ):
        with pytest.raises(CommandError, match="Коммерческий запуск заблокирован"):
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
    assert len(payload["gates"]) == len(EXPECTED_GATES)
    assert [item["gate"] for item in payload["gates"]] == [
        "django_deploy_check",
        "semantic_model",
        "commercial_config",
        "advertised_features",
        "customer_trust",
        "payment_commercial",
        "economic_safety",
        "financial_invariants",
        "operational_drills",
        "public_legal",
        "prelaunch_strict",
    ]


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
