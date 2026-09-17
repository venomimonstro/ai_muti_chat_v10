from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .mfa import (
    consume_recovery_code,
    decrypt_secret,
    encrypt_secret,
    mark_session_verified,
    new_recovery_codes,
    new_secret,
    otpauth_uri,
    recovery_hash,
    session_verified,
    verify_totp,
)
from .models import UserSecurityProfile


def _profile(user):
    profile, _ = UserSecurityProfile.objects.get_or_create(user=user)
    return profile


def _admin(user):
    return bool(user.is_staff or user.role == user.Role.PLATFORM_ADMIN)


class MFAStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile = _profile(request.user)
        return Response({
            "available": _admin(request.user),
            "enabled": profile.mfa_enabled,
            "session_verified": session_verified(request),
            "recovery_codes_remaining": len(profile.recovery_code_hashes or []),
        })


class MFASetupView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not _admin(request.user):
            return Response({"detail": "MFA setup is available for administrators"}, status=403)
        password = str(request.data.get("password", ""))
        if authenticate(username=request.user.username, password=password) is None:
            return Response({"detail": "Неверный пароль"}, status=400)
        secret = new_secret()
        profile = _profile(request.user)
        profile.totp_secret_encrypted = encrypt_secret(secret)
        profile.mfa_enabled = False
        profile.recovery_code_hashes = []
        profile.mfa_enabled_at = None
        profile.save()
        return Response({"secret": secret, "otpauth_uri": otpauth_uri(secret=secret, email=request.user.email)})


class MFAConfirmView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        profile = UserSecurityProfile.objects.select_for_update().filter(user=request.user).first()
        if not profile or not profile.totp_secret_encrypted:
            return Response({"detail": "Сначала начните настройку MFA"}, status=400)
        try:
            secret = decrypt_secret(profile.totp_secret_encrypted)
        except ValueError:
            return Response({"detail": "MFA secret unavailable"}, status=500)
        if not verify_totp(secret, str(request.data.get("code", ""))):
            return Response({"detail": "Неверный код"}, status=400)
        codes = new_recovery_codes()
        profile.mfa_enabled = True
        profile.mfa_enabled_at = timezone.now()
        profile.last_mfa_at = timezone.now()
        profile.recovery_code_hashes = [recovery_hash(code) for code in codes]
        profile.save()
        mark_session_verified(request)
        return Response({"enabled": True, "recovery_codes": codes})


class MFAVerifyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        profile = UserSecurityProfile.objects.select_for_update().filter(user=request.user, mfa_enabled=True).first()
        if not profile:
            return Response({"detail": "MFA не настроена"}, status=400)
        code = str(request.data.get("code", ""))
        recovery = str(request.data.get("recovery_code", ""))
        valid = False
        if code:
            try:
                valid = verify_totp(decrypt_secret(profile.totp_secret_encrypted), code)
            except ValueError:
                valid = False
        elif recovery:
            valid = consume_recovery_code(profile, recovery)
        if not valid:
            return Response({"detail": "Неверный MFA код"}, status=400)
        profile.last_mfa_at = timezone.now()
        profile.save(update_fields=["last_mfa_at", "updated_at"])
        mark_session_verified(request)
        return Response({"verified": True})
