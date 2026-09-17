from django.conf import settings
from rest_framework.permissions import BasePermission

from apps.accounts.mfa import session_verified
from apps.accounts.models import User, UserSecurityProfile


class IsPlatformAdmin(BasePermission):
    message = "Administrator access requires an active account and MFA verification"

    def has_permission(self, request, view):
        user = request.user
        is_admin = bool(
            user
            and user.is_authenticated
            and user.status == User.Status.ACTIVE
            and (user.is_staff or user.role == User.Role.PLATFORM_ADMIN)
        )
        if not is_admin:
            return False
        if not settings.ADMIN_MFA_ENFORCED:
            return True
        profile = UserSecurityProfile.objects.filter(user=user, mfa_enabled=True).only("id").first()
        return bool(profile and session_verified(request))
