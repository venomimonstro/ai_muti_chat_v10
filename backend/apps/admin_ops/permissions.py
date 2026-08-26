from rest_framework.permissions import BasePermission

from apps.accounts.models import User


class IsPlatformAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.status == User.Status.ACTIVE
            and (request.user.is_staff or request.user.role == User.Role.PLATFORM_ADMIN)
        )
