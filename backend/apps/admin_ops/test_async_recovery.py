from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.billing.models import BalanceReservation
from apps.billing.services import credit
from apps.files.models import FileAsset
from apps.image_studio.models import ImageGeneration, ImageModel
from apps.image_studio.services import prepare_generation
from apps.projects.models import Project

from .recovery import recover_stale_operations


@pytest.mark.django_db(transaction=True)
def test_stale_queued_image_releases_reservation_and_fails(settings):
    settings.IMAGES_ENABLED = True
    settings.IMAGE_CONFIRM_THRESHOLD_RUB = "1000"
    settings.OPERATION_STALE_TIMEOUT_SECONDS = 60
    provider = Provider.objects.create(slug="stale-image-provider", name="Stale image")
    model = ImageModel.objects.create(
        provider=provider,
        slug="stale-image-model",
        display_name="Stale image model",
        upstream_model="image-v1",
        provider_price_per_image=Decimal("1"),
        supported_sizes=["1024x1024"],
        supported_qualities=["standard"],
    )
    user = User.objects.create_user(
        username="stale-image-user", email="stale-image@example.test", password="password123"
    )
    credit(user, Decimal("100"), "test", "stale-image")
    generation, created = prepare_generation(
        user=user,
        model_slug=model.slug,
        prompt="stale",
        size="1024x1024",
        quality="standard",
        count=1,
        idempotency_key="stale:image:queued",
        confirmed=True,
        deferred=True,
    )
    assert created is True
    ImageGeneration.objects.filter(pk=generation.pk).update(
        created_at=timezone.now() - timedelta(minutes=10)
    )

    result = recover_stale_operations()

    generation.refresh_from_db()
    generation.reservation.refresh_from_db()
    user.wallet.refresh_from_db()
    assert result["image_generations"] == 1
    assert generation.state == ImageGeneration.State.FAILED
    assert generation.error_code == "stale_operation_recovered"
    assert generation.reservation.state == BalanceReservation.State.RELEASED
    assert user.wallet.available_rub == Decimal("100.0000")
    assert user.wallet.reserved_rub == Decimal("0.0000")


@pytest.mark.django_db(transaction=True)
def test_stale_uploaded_file_becomes_failed_instead_of_waiting_forever(settings):
    settings.OPERATION_STALE_TIMEOUT_SECONDS = 60
    user = User.objects.create_user(
        username="stale-file-user", email="stale-file@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Stale file project")
    asset = FileAsset.objects.create(
        owner=user,
        project=project,
        blob="users/test/stale.txt",
        original_name="stale.txt",
        declared_content_type="text/plain",
        detected_type="text",
        size_bytes=5,
        sha256="a" * 64,
        status=FileAsset.Status.UPLOADED,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="stale:file:uploaded",
    )
    FileAsset.objects.filter(pk=asset.pk).update(
        updated_at=timezone.now() - timedelta(minutes=10)
    )

    result = recover_stale_operations()

    asset.refresh_from_db()
    assert result["files"] == 1
    assert asset.status == FileAsset.Status.FAILED
    assert asset.error_code == "stale_operation_recovered"
