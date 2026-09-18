import logging
import os
from urllib.parse import urlencode

from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.billing.promotions import grant_signup_promo

from .models import User

logger = logging.getLogger(__name__)
VERIFY_SALT = "accounts.email.verify.v1"
VERIFY_MAX_AGE = 60 * 60 * 24
RESET_GENERATOR = PasswordResetTokenGenerator()


def _frontend_url(path: str, params: dict) -> str:
    base = os.getenv("FRONTEND_PUBLIC_URL", "http://localhost:3000").rstrip("/")
    return f"{base}{path}?{urlencode(params)}"


def _send(subject: str, body: str, recipient: str):
    backend = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
    connection = get_connection(
        backend=backend,
        host=os.getenv("EMAIL_HOST") or None,
        port=int(os.getenv("EMAIL_PORT", "587")),
        username=os.getenv("EMAIL_HOST_USER") or None,
        password=os.getenv("EMAIL_HOST_PASSWORD") or None,
        use_tls=os.getenv("EMAIL_USE_TLS", "true").lower() == "true",
    )
    EmailMessage(
        subject=subject,
        body=body,
        from_email=os.getenv("DEFAULT_FROM_EMAIL", "noreply@localhost"),
        to=[recipient],
        connection=connection,
    ).send(fail_silently=False)


def _safe_send(subject: str, body: str, recipient: str) -> bool:
    try:
        _send(subject, body, recipient)
        return True
    except Exception:
        logger.exception(
            "Account email delivery failed",
            extra={
                "recipient_domain": recipient.rsplit("@", 1)[-1]
                if "@" in recipient
                else "invalid"
            },
        )
        return False


def send_verification_email(user: User) -> bool:
    token = signing.dumps(
        {"user_id": str(user.id), "email": user.email, "password": user.password[-12:]},
        salt=VERIFY_SALT,
        compress=True,
    )
    url = _frontend_url("/verify-email", {"token": token})
    return _safe_send(
        "Подтвердите email",
        f"Подтвердите адрес электронной почты:\n\n{url}\n\nСсылка действует 24 часа.",
        user.email,
    )


class EmailVerificationRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        email = str(request.data.get("email", "")).strip().casefold()
        user = User.objects.filter(email=email, status=User.Status.ACTIVE).first()
        if user and not user.email_verified:
            send_verification_email(user)
        return Response({"detail": "Если адрес доступен для подтверждения, письмо отправлено"})


class EmailVerificationConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        token = str(request.data.get("token", ""))
        try:
            payload = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_MAX_AGE)
            user = User.objects.get(pk=payload["user_id"], email=payload["email"])
            if payload.get("password") != user.password[-12:]:
                raise signing.BadSignature("password changed")
        except (signing.BadSignature, signing.SignatureExpired, User.DoesNotExist, KeyError):
            return Response({"detail": "Ссылка недействительна или истекла"}, status=400)
        promo_granted = False
        if not user.email_verified:
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
            promo_granted = grant_signup_promo(user) is not None
        return Response({"verified": True, "promo_granted": promo_granted})


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        email = str(request.data.get("email", "")).strip().casefold()
        user = User.objects.filter(email=email, status=User.Status.ACTIVE).first()
        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = RESET_GENERATOR.make_token(user)
            url = _frontend_url("/reset-password", {"uid": uid, "token": token})
            _safe_send(
                "Сброс пароля",
                f"Для смены пароля откройте ссылку:\n\n{url}\n\nЕсли это были не вы, проигнорируйте письмо.",
                user.email,
            )
        return Response({"detail": "Если аккаунт существует, инструкция отправлена"})


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        uid = str(request.data.get("uid", ""))
        token = str(request.data.get("token", ""))
        password = str(request.data.get("new_password", ""))
        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id, status=User.Status.ACTIVE)
        except (ValueError, TypeError, User.DoesNotExist):
            return Response({"detail": "Ссылка недействительна или истекла"}, status=400)
        if not RESET_GENERATOR.check_token(user, token):
            return Response({"detail": "Ссылка недействительна или истекла"}, status=400)
        try:
            validate_password(password, user=user)
        except Exception as exc:
            messages = getattr(exc, "messages", [str(exc)])
            return Response({"new_password": messages}, status=400)
        user.set_password(password)
        user.save(update_fields=["password"])
        user_id = str(user.id)
        for session in Session.objects.filter(expire_date__gte=timezone.now()):
            try:
                if session.get_decoded().get("_auth_user_id") == user_id:
                    session.delete()
            except Exception:
                continue
        return Response({"reset": True})
