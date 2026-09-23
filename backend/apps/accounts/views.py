from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.sessions.models import Session
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from apps.memory_store.models import MemoryCandidate

from .mfa import SESSION_MFA_KEY
from .models import Notification, SupportRequest, User, UserPreference
from .security_views import send_verification_email
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    NotificationSerializer,
    RegisterSerializer,
    SupportRequestSerializer,
    UserPreferenceSerializer,
    UserSerializer,
)


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    @transaction.atomic
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        login(request, user)
        request.session.pop(SESSION_MFA_KEY, None)
        transaction.on_commit(lambda: send_verification_email(user))
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        # Browser tabs share the same Django session cookie. A platform admin who
        # signs into a client account in another tab must never have the real admin
        # session replaced. Instead authenticate the supplied credentials, leave
        # the admin session untouched and let that tab switch to X-Test-User mode.
        real_user = getattr(request, "user", None)
        if (
            getattr(real_user, "is_authenticated", False)
            and getattr(real_user, "role", None) == User.Role.PLATFORM_ADMIN
            and real_user.id != user.id
        ):
            if user.role == User.Role.PLATFORM_ADMIN:
                return Response(
                    {"detail": "Другого администратора платформы нельзя открывать в пользовательском тестовом режиме."},
                    status=status.HTTP_409_CONFLICT,
                )
            payload = UserSerializer(user).data
            payload["test_user_mode"] = True
            payload["test_user_id"] = str(user.id)
            response = Response(payload)
            response["X-Test-User-Active"] = "1"
            response["X-Test-User-Id"] = str(user.id)
            return response

        login(request, user)
        request.session.pop(SESSION_MFA_KEY, None)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # PlatformAdminTestUserMiddleware intercepts tab-scoped impersonated
        # logout before this view. A request reaching here is a real logout.
        request.session.pop(SESSION_MFA_KEY, None)
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class CsrfView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"csrf_token": get_token(request)})


class PreferenceView(APIView):
    def get_object(self, request):
        preference, _ = UserPreference.objects.get_or_create(user=request.user)
        return preference

    def get(self, request):
        return Response(UserPreferenceSerializer(self.get_object(request)).data)

    def patch(self, request):
        preference = self.get_object(request)
        candidates_were_enabled = preference.auto_memory_enabled and preference.memory_enabled
        serializer = UserPreferenceSerializer(preference, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        candidates_are_enabled = serializer.instance.auto_memory_enabled and serializer.instance.memory_enabled
        if candidates_were_enabled and not candidates_are_enabled:
            MemoryCandidate.objects.filter(
                owner=request.user,
                status__in=[MemoryCandidate.Status.PENDING, MemoryCandidate.Status.CONFLICT],
            ).update(status=MemoryCandidate.Status.DISMISSED, reviewed_at=timezone.now())
        return Response(serializer.data)


class ChangePasswordView(APIView):
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        request.session.pop(SESSION_MFA_KEY, None)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # Impersonated requests are intercepted by middleware, so this can only
        # revoke sessions for the real authenticated account.
        user_id = str(request.user.id)
        for session in Session.objects.filter(expire_date__gte=timezone.now()):
            if session.get_decoded().get("_auth_user_id") == user_id:
                session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationViewSet(ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    @action(detail=True, methods=["post"])
    def read(self, _request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, _request):
        self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupportRequestViewSet(ModelViewSet):
    serializer_class = SupportRequestSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return SupportRequest.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
