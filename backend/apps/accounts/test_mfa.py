import time

import pytest
from django.test import RequestFactory, override_settings
from django.contrib.sessions.middleware import SessionMiddleware

from apps.admin_ops.permissions import IsPlatformAdmin

from .mfa import encrypt_secret, decrypt_secret, mark_session_verified, new_secret, totp_code, verify_totp
from .models import User, UserSecurityProfile


@pytest.mark.django_db
def test_totp_roundtrip_and_encrypted_secret():
    secret = new_secret()
    encrypted = encrypt_secret(secret)
    assert encrypted != secret
    assert decrypt_secret(encrypted) == secret
    code = totp_code(secret, int(time.time()))
    assert verify_totp(secret, code)
    assert not verify_totp(secret, "000000") or code == "000000"


def _request(user):
    request = RequestFactory().get("/api/v1/admin/overview/")
    middleware = SessionMiddleware(lambda req: None)
    middleware.process_request(request)
    request.session.save()
    request.user = user
    return request


@pytest.mark.django_db
@override_settings(ADMIN_MFA_ENFORCED=True)
def test_admin_permission_requires_enabled_and_verified_mfa():
    user = User.objects.create_user(
        username="admin-mfa",
        email="admin-mfa@example.test",
        password="strong-password-123",
        is_staff=True,
        role=User.Role.PLATFORM_ADMIN,
    )
    request = _request(user)
    permission = IsPlatformAdmin()
    assert not permission.has_permission(request, None)
    UserSecurityProfile.objects.create(user=user, mfa_enabled=True)
    assert not permission.has_permission(request, None)
    mark_session_verified(request)
    assert permission.has_permission(request, None)
