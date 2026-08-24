from django.urls import path
from rest_framework.routers import DefaultRouter

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
] + router.urls
