import os
from urllib.parse import urlencode

from django.core import signing
from django.core.exceptions import ImproperlyConfigured, ValidationError as DjangoValidationError
from django.http import HttpResponseRedirect
from rest_framework.exceptions import ValidationError
from rest_framework.views import APIView

from .models import GitHubInstallation
from .services import exchange_user_code, verified_installation
from .views import STATE_MAX_AGE, STATE_SALT

OAUTH_STATE_SALT = "github.integration.oauth.v1"


def _frontend_projects_url(status):
    base = os.getenv("FRONTEND_PUBLIC_URL", "http://localhost:3000").rstrip("/")
    return f"{base}/app/projects?{urlencode({'github': status})}"


class GitHubSetupView(APIView):
    """GitHub App setup URL: installation -> verified OAuth authorization."""

    def get(self, request):
        state = str(request.query_params.get("state") or "").strip()
        installation_id = request.query_params.get("installation_id")
        setup_action = str(request.query_params.get("setup_action") or "").strip()
        if not state or not installation_id:
            raise ValidationError("GitHub setup callback is incomplete")
        try:
            payload = signing.loads(state, salt=STATE_SALT, max_age=STATE_MAX_AGE)
        except (signing.BadSignature, signing.SignatureExpired) as exc:
            raise ValidationError("GitHub state is invalid or expired") from exc
        if payload.get("user_id") != str(request.user.id):
            raise ValidationError("GitHub state belongs to another user")
        try:
            normalized_installation_id = int(installation_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("GitHub installation id is invalid") from exc
        if normalized_installation_id <= 0:
            raise ValidationError("GitHub installation id is invalid")
        if setup_action not in {"install", "update", "request", ""}:
            raise ValidationError("Unsupported GitHub setup action")

        oauth_state = signing.dumps(
            {
                "user_id": str(request.user.id),
                "installation_id": normalized_installation_id,
            },
            salt=OAUTH_STATE_SALT,
            compress=True,
        )
        client_id = os.getenv("GITHUB_APP_CLIENT_ID", "").strip()
        if not client_id:
            raise ValidationError("GitHub App client id is not configured")
        url = "https://github.com/login/oauth/authorize?" + urlencode(
            {
                "client_id": client_id,
                "state": oauth_state,
            }
        )
        return HttpResponseRedirect(url)


class GitHubOAuthCallbackView(APIView):
    """GitHub user authorization callback. No user token is persisted."""

    def get(self, request):
        code = str(request.query_params.get("code") or "").strip()
        state = str(request.query_params.get("state") or "").strip()
        if not code or not state:
            raise ValidationError("GitHub OAuth callback is incomplete")
        try:
            payload = signing.loads(state, salt=OAUTH_STATE_SALT, max_age=STATE_MAX_AGE)
        except (signing.BadSignature, signing.SignatureExpired) as exc:
            raise ValidationError("GitHub OAuth state is invalid or expired") from exc
        if payload.get("user_id") != str(request.user.id):
            raise ValidationError("GitHub OAuth state belongs to another user")
        try:
            installation_id = int(payload["installation_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("GitHub installation id is invalid") from exc

        try:
            user_token = exchange_user_code(code)
            installation = verified_installation(user_token, installation_id)
        except (DjangoValidationError, ImproperlyConfigured) as exc:
            raise ValidationError(str(exc)) from exc

        existing = GitHubInstallation.objects.filter(installation_id=installation_id).first()
        if existing is not None and existing.owner_id != request.user.id:
            raise ValidationError("Эта GitHub installation уже привязана к другому аккаунту сервиса")
        account = installation.get("account") or {}
        defaults = {
            "account_login": str(account.get("login") or "")[:255],
            "account_type": str(account.get("type") or "")[:40],
            "repository_selection": str(installation.get("repository_selection") or "")[:24],
            "permissions": installation.get("permissions") or {},
            "active": True,
        }
        if existing is None:
            GitHubInstallation.objects.create(
                installation_id=installation_id,
                owner=request.user,
                **defaults,
            )
        else:
            for field, value in defaults.items():
                setattr(existing, field, value)
            existing.save(update_fields=[*defaults.keys(), "updated_at"])
        return HttpResponseRedirect(_frontend_projects_url("connected"))
