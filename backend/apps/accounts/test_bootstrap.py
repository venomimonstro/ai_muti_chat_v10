import pytest
from django.core.management import call_command

from .models import User


@pytest.mark.django_db
def test_bootstrap_admin_is_idempotent(monkeypatch):
    monkeypatch.setenv("AIWORKSPACE_ADMIN_PASSWORD", "long-install-password")
    call_command("bootstrap_admin", username="owner", email="OWNER@example.com")
    user = User.objects.get(username="owner")
    assert user.email == "owner@example.com"
    assert user.is_superuser and user.is_staff
    assert user.role == User.Role.PLATFORM_ADMIN
    assert user.check_password("long-install-password")

    monkeypatch.setenv("AIWORKSPACE_ADMIN_PASSWORD", "different-password")
    call_command("bootstrap_admin", username="owner", email="owner@example.com")
    user.refresh_from_db()
    assert user.check_password("long-install-password")

    call_command(
        "bootstrap_admin",
        username="owner",
        email="new-owner@example.com",
        reset_password=True,
    )
    user.refresh_from_db()
    assert user.email == "new-owner@example.com"
    assert user.check_password("different-password")
