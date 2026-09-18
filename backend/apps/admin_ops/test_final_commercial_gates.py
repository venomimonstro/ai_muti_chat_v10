from datetime import timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.admin_ops.models import BackupRecord, OperationalDrillEvidence, ReleaseRecord


@pytest.mark.django_db
def test_operational_drills_gate_requires_complete_recent_evidence():
    admin = User.objects.create_user(
        username="ops-admin",
        email="ops@example.test",
        password="strong-test-password",
        role=User.Role.PLATFORM_ADMIN,
        is_staff=True,
    )
    now = timezone.now()
    BackupRecord.objects.create(
        kind=BackupRecord.Kind.FULL,
        status=BackupRecord.Status.RESTORED,
        storage_reference="restore://test",
        requested_by=admin,
        started_at=now,
        completed_at=now,
        verified_at=now,
        restored_at=now,
    )
    ReleaseRecord.objects.create(
        version="rollback-drill-test",
        commit_sha="a" * 40,
        environment="production",
        state=ReleaseRecord.State.ROLLED_BACK,
        health_snapshot={"drill": True},
        created_by=admin,
    )
    for kind in OperationalDrillEvidence.Kind.values:
        OperationalDrillEvidence.objects.create(
            kind=kind,
            evidence_reference=f"evidence://{kind}",
            passed_at=now,
            recorded_by=admin,
        )

    call_command("operational_drills_check", max_age_hours=24)


@pytest.mark.django_db
def test_operational_drills_gate_blocks_stale_evidence():
    admin = User.objects.create_user(
        username="stale-admin",
        email="stale@example.test",
        password="strong-test-password",
        role=User.Role.PLATFORM_ADMIN,
        is_staff=True,
    )
    stale = timezone.now() - timedelta(days=10)
    OperationalDrillEvidence.objects.create(
        kind=OperationalDrillEvidence.Kind.LOAD,
        evidence_reference="evidence://stale-load",
        passed_at=stale,
        recorded_by=admin,
    )

    with pytest.raises(CommandError):
        call_command("operational_drills_check", max_age_hours=24)


def _httpx_context(response):
    client = Mock()
    client.get.return_value = response
    context = MagicMock()
    context.__enter__.return_value = client
    context.__exit__.return_value = False
    return context


def test_public_legal_gate_accepts_complete_published_pages(settings):
    settings.FRONTEND_PUBLIC_URL = "https://ai.example.com"
    response = Mock(status_code=200, text="Юридический документ " * 80)
    context = _httpx_context(response)

    with patch(
        "apps.admin_ops.management.commands.public_legal_check.httpx.Client",
        return_value=context,
    ):
        call_command("public_legal_check")


def test_public_legal_gate_blocks_placeholder(settings):
    settings.FRONTEND_PUBLIC_URL = "https://ai.example.com"
    response = Mock(
        status_code=200,
        text=("Юридический документ " * 40) + "Реквизиты продавца не настроены",
    )
    context = _httpx_context(response)

    with patch(
        "apps.admin_ops.management.commands.public_legal_check.httpx.Client",
        return_value=context,
    ):
        with pytest.raises(CommandError):
            call_command("public_legal_check")
