import logging
import uuid

from django.http import JsonResponse

from .models import User

logger = logging.getLogger(__name__)

TEST_USER_HEADER = "HTTP_X_TEST_USER"
_SKIP_PATHS = {
    "/api/v1/auth/login/",
    "/api/v1/auth/register/",
    "/api/v1/auth/logout/",
    "/api/v1/auth/logout-all/",
    "/api/v1/auth/csrf/",
}


class PlatformAdminTestUserMiddleware:
    """Allow a platform admin to test client APIs as another user per browser tab.

    The real authenticated session remains the platform admin. A client tab can
    send X-Test-User with a target UUID; only non-admin API routes are
    impersonated. The frontend stores this UUID in sessionStorage, which is
    isolated per tab, so an admin console tab and a client test tab can coexist.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.real_user = request.user
        raw_target = request.META.get(TEST_USER_HEADER, "").strip()
        path = request.path

        if (
            raw_target
            and path.startswith("/api/v1/")
            and not path.startswith("/api/v1/admin/")
            and path not in _SKIP_PATHS
        ):
            real_user = request.user
            if not getattr(real_user, "is_authenticated", False):
                return JsonResponse({"detail": "Тестовый режим требует авторизации администратора"}, status=401)
            if getattr(real_user, "role", None) != User.Role.PLATFORM_ADMIN:
                return JsonResponse({"detail": "Тестовый режим доступен только администратору платформы"}, status=403)
            try:
                target_id = uuid.UUID(raw_target)
            except (ValueError, TypeError, AttributeError):
                return JsonResponse({"detail": "Некорректный идентификатор тестового пользователя"}, status=400)

            target = User.objects.filter(id=target_id, status=User.Status.ACTIVE).first()
            if target is None:
                return JsonResponse({"detail": "Тестовый пользователь не найден или не активен"}, status=404)
            if target.role == User.Role.PLATFORM_ADMIN:
                return JsonResponse({"detail": "Нельзя запускать клиентский тестовый режим от имени другого platform admin"}, status=403)

            request.user = target
            request.test_user_mode = True
            request.test_user_admin = real_user
            logger.info(
                "platform_admin_test_user admin_id=%s target_user_id=%s path=%s method=%s",
                real_user.id,
                target.id,
                path,
                request.method,
            )

        response = self.get_response(request)
        if getattr(request, "test_user_mode", False):
            response["X-Test-User-Active"] = "1"
            response["X-Test-User-Id"] = str(request.user.id)
        return response
