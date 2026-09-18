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


def _request_path(request):
    if request.method in {"GET", "HEAD"}:
        return request.GET.get("path", "")
    if not request.body:
        return ""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return ""
    return payload.get("path", "") if isinstance(payload, dict) else ""


def github_guard(view, *, protect_path=False):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not integration_enabled():
            return JsonResponse(
                {"detail": "GitHub интеграция отключена администратором"},
                status=503,
            )
        if protect_path and not sensitive_path_allowed():
            path = _request_path(request)
            if is_sensitive_path(path):
                return JsonResponse(
                    {
                        "detail": (
                            "Доступ к секретам и GitHub Actions заблокирован политикой безопасности"
                        )
                    },
                    status=403,
                )
        return view(request, *args, **kwargs)

    return wrapped
