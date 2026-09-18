import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from .models import CostAnomaly


@pytest.mark.django_db
def test_economic_gate_blocks_unresolved_critical_anomaly():
    CostAnomaly.objects.create(
        kind=CostAnomaly.Kind.COST_DEVIATION,
        severity="critical",
        dedupe_key="gate:critical:open",
        provider_slug="provider-x",
        model_slug="model-x",
        details={"reason": "unexpected provider cost"},
    )

    with pytest.raises(CommandError, match="unresolved_critical_cost_anomalies=1"):
        call_command("economic_safety_check")


@pytest.mark.django_db
def test_acknowledged_critical_anomaly_does_not_permanently_brick_gate():
    CostAnomaly.objects.create(
        kind=CostAnomaly.Kind.COST_DEVIATION,
        severity="critical",
        status=CostAnomaly.Status.ACKNOWLEDGED,
        dedupe_key="gate:critical:acknowledged",
        provider_slug="provider-safe",
        model_slug="model-safe",
        details={"reason": "reviewed historical incident"},
    )

    call_command("economic_safety_check")
