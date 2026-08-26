from rest_framework.permissions import BasePermission

from .models import User


class IsActiveUser(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.status == User.Status.ACTIVE
        )
