from io import StringIO
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User

from .models import LedgerEntry
from .services import credit


@pytest.mark.django_db(transaction=True)
def test_billing_integrity_check_passes_for_reconciled_wallet():
    user = User.objects.create_user(username="billing-integrity-ok", email="billing-ok@example.test")
    credit(user, Decimal("100.00"), "payment", "ok-payment", bucket="paid")
    stdout = StringIO()

    call_command("billing_integrity_check", stdout=stdout)

    output = stdout.getvalue()
    assert "FAILURES=0" in output
    assert "BILLING_INTEGRITY_OK" in output


@pytest.mark.django_db(transaction=True)
def test_billing_integrity_check_exits_nonzero_on_ledger_mismatch():
    user = User.objects.create_user(username="billing-integrity-bad", email="billing-bad@example.test")
    entry = credit(user, Decimal("100.00"), "payment", "bad-payment", bucket="paid")
    # Simulate storage corruption/import damage. QuerySet.update intentionally
    # bypasses LedgerEntry.save() immutability so the audit sees a real mismatch.
    LedgerEntry.objects.filter(pk=entry.pk).update(available_delta_rub=Decimal("99.00"))
    stdout = StringIO()

    with pytest.raises(CommandError, match="BILLING_INTEGRITY_FAILED"):
        call_command("billing_integrity_check", stdout=stdout)

    output = stdout.getvalue()
    assert "ledger_available_mismatch" in output
    assert "FAILURES=1" in output
