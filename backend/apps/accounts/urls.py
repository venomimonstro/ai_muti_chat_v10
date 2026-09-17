from django.urls import path
from rest_framework.routers import DefaultRouter

from .security_views import (
    EmailVerificationConfirmView,
    EmailVerificationRequestView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    SessionListView,
    SessionRevokeView,
)
from .views import (
    ChangePasswordView,
    CsrfView,
    LoginView,
    LogoutAllView,
    LogoutView,
    MeView,
    NotificationViewSet,
    PreferenceView,
    RegisterView,
    SupportRequestViewSet,
)

router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")
router.register("support", SupportRequestViewSet, basename="support")

urlpatterns = [
    path("register/", RegisterView.as_view()),
    path("login/", LoginView.as_view()),
    path("logout/", LogoutView.as_view()),
    path("me/", MeView.as_view()),
    path("csrf/", CsrfView.as_view()),
    path("preferences/", PreferenceView.as_view()),
    path("change-password/", ChangePasswordView.as_view()),
    path("logout-all/", LogoutAllView.as_view()),
    path("verify-email/request/", EmailVerificationRequestView.as_view()),
    path("verify-email/confirm/", EmailVerificationConfirmView.as_view()),
    path("password-reset/request/", PasswordResetRequestView.as_view()),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view()),
    path("sessions/", SessionListView.as_view()),
    path("sessions/<str:session_key>/revoke/", SessionRevokeView.as_view()),
] + router.urls
