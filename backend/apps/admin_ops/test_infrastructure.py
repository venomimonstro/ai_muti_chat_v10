import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User

from .infrastructure import infrastructure_health
from .tasks import WORKER_HEARTBEAT_KEY


@pytest.mark.django_db
def test_infrastructure_endpoint_requires_platform_admin(settings):
    settings.ADMIN_MFA_ENFORCED = False
    user = User.objects.create_user(
        username="regular-user",
        email="regular@example.test",
        password="password123!",
    )
    client = APIClient()
    client.force_authenticate(user)
    response = client.get("/api/v1/admin/infrastructure/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_sees_database_cache_and_worker_heartbeat(settings):
    settings.ADMIN_MFA_ENFORCED = False
    admin = User.objects.create_user(
        username="infra-admin",
        email="infra-admin@example.test",
        password="password123!",
        is_staff=True,
    )
    cache.set(WORKER_HEARTBEAT_KEY, timezone.now().isoformat(), timeout=180)
    client = APIClient()
    client.force_authenticate(admin)
    response = client.get("/api/v1/admin/infrastructure/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["database"]["ok"] is True
    assert payload["cache"]["ok"] is True
    assert payload["background_tasks"]["ok"] is True
    assert payload["critical_ok"] is True


@pytest.mark.django_db
def test_strict_system_health_blocks_missing_worker_heartbeat():
    cache.delete(WORKER_HEARTBEAT_KEY)
    with pytest.raises(CommandError, match="heartbeat"):
        call_command("system_health_check", "--strict")


@pytest.mark.django_db
def test_strict_system_health_passes_with_fresh_heartbeat():
    cache.set(WORKER_HEARTBEAT_KEY, timezone.now().isoformat(), timeout=180)
    health = infrastructure_health()
    assert health["critical_ok"] is True
    call_command("system_health_check", "--strict")
