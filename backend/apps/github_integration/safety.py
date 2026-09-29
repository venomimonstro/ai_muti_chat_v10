import json
import os
from functools import wraps

from django.http import JsonResponse


_SENSITIVE_BASENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_SENSITIVE_PREFIXES = (
    ".github/workflows/",
    ".github/actions/",
)


def integration_enabled():
    return os.getenv("GITHUB_INTEGRATION_ENABLED", "false").strip().lower() == "true"


def sensitive_path_allowed():
    return os.getenv("GITHUB_ALLOW_SENSITIVE_PATHS", "false").strip().lower() == "true"


def normalize_repo_path(value):
    return str(value or "").strip().lstrip("/").replace("\\", "/")


def is_sensitive_path(value):
    path = normalize_repo_path(value)
    lowered = path.casefold()
    if not path:
        return False
    basename = lowered.rsplit("/", 1)[-1]
    if basename in _SENSITIVE_BASENAMES:
        return True
    if basename.startswith(".env."):
        return True
    if basename.endswith(_SENSITIVE_SUFFIXES):
        return True
    if any(lowered.startswith(prefix) for prefix in _SENSITIVE_PREFIXES):
        return True
    return False


def _request_json(request):
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _request_path(request):
    if request.method in {"GET", "HEAD"}:
        return request.GET.get("path", "")
    return _request_json(request).get("path", "")


def _unverified_organization(installation):
    return bool(
        installation
        and (installation.account_type or "").casefold() == "organization"
        and not installation.user_verified_at
    )


def _organization_scope_blocked(request, kwargs):
    from .models import GitHubInstallation, GitHubRepositoryBinding

    installation_pk = kwargs.get("installation_id")
    if installation_pk:
        installation = (
            GitHubInstallation.objects.filter(pk=installation_pk)
            .only("account_type", "user_verified_at")
            .first()
        )
        if _unverified_organization(installation):
            return True

    project_id = kwargs.get("project_id")
    if project_id:
        binding = (
            GitHubRepositoryBinding.objects.filter(project_id=project_id)
            .select_related("installation")
            .only("installation__account_type", "installation__user_verified_at")
            .first()
        )
        if binding and _unverified_organization(binding.installation):
            return True
        if request.method == "POST" and binding is None:
            installation_pk = _request_json(request).get("installation")
            if installation_pk:
                installation = (
                    GitHubInstallation.objects.filter(pk=installation_pk)
                    .only("account_type", "user_verified_at")
                    .first()
                )
                if _unverified_organization(installation):
                    return True
    return False


def _json_error(detail, status):
    # github_guard wraps APIView.as_view() from the outside, so it runs before
    # DRF content negotiation sets accepted_renderer on a DRF Response. A plain
    # Django JsonResponse is therefore required for fail-closed guard exits.
    return JsonResponse({"detail": detail}, status=status)


def github_guard(view, *, protect_path=False):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not integration_enabled():
            return _json_error("GitHub интеграция отключена администратором", 503)
        if _organization_scope_blocked(request, kwargs):
            return _json_error(
                "GitHub-репозиторий организации требует повторного user-scoped OAuth-подтверждения",
                403,
            )
        if protect_path and not sensitive_path_allowed():
            path = _request_path(request)
            if is_sensitive_path(path):
                return _json_error(
                    "Доступ к секретам и GitHub Actions заблокирован политикой безопасности",
                    403,
                )
        return view(request, *args, **kwargs)

    return wrapped
