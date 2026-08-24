from decimal import Decimal

from django.conf import settings
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.sessions.models import Session
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from apps.billing.services import credit

from .models import Notification, SupportRequest, UserPreference
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

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        credit(
            user,
            Decimal(settings.SIGNUP_PROMO_RUB),
            "signup_promo",
            f"signup:{user.id}",
            bucket="promo",
        )
        login(request, user)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    def post(self, request):
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
        serializer = UserPreferenceSerializer(
            self.get_object(request), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class ChangePasswordView(APIView):
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    def post(self, request):
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
