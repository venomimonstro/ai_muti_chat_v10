import base64
import json
import os
import time
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.core.exceptions import ImproperlyConfigured, ValidationError

GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"
API_VERSION = "2022-11-28"
TIMEOUT = 15.0


def _env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} is not configured")
    return value


def configured():
    return all(os.getenv(name, "").strip() for name in (
        "GITHUB_APP_ID",
        "GITHUB_APP_SLUG",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_CLIENT_ID",
        "GITHUB_APP_CLIENT_SECRET",
    ))


def install_url(state):
    slug = _env("GITHUB_APP_SLUG")
    return f"{GITHUB_WEB}/apps/{quote(slug, safe='')}/installations/new?state={quote(state, safe='')}"


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def app_jwt():
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": _env("GITHUB_APP_ID")}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    private_key = serialization.load_pem_private_key(
        _env("GITHUB_APP_PRIVATE_KEY").replace("\\n", "\n").encode(),
        password=None,
    )
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def _headers(token):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "AIWorkspace-GitHubApp/1.0",
    }


def exchange_user_code(code):
    response = httpx.post(
        f"{GITHUB_WEB}/login/oauth/access_token",
        data={
            "client_id": _env("GITHUB_APP_CLIENT_ID"),
            "client_secret": _env("GITHUB_APP_CLIENT_SECRET"),
            "code": code,
        },
        headers={"Accept": "application/json", "User-Agent": "AIWorkspace-GitHubApp/1.0"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("access_token") or "")
    if not token:
        raise ValidationError("GitHub authorization failed")
    return token


def verified_installation(user_token, installation_id):
    response = httpx.get(
        f"{GITHUB_API}/user/installations",
        headers=_headers(user_token),
        params={"per_page": 100},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    for installation in response.json().get("installations", []):
        if int(installation.get("id", 0)) == int(installation_id):
            return installation
    raise ValidationError("GitHub installation is not owned by the authorized user")


def installation_token(installation_id, *, repository_ids=None, permissions=None):
    body = {}
    if repository_ids:
        body["repository_ids"] = [int(value) for value in repository_ids]
    if permissions:
        body["permissions"] = permissions
    response = httpx.post(
        f"{GITHUB_API}/app/installations/{int(installation_id)}/access_tokens",
        headers=_headers(app_jwt()),
        json=body,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    token = str(response.json().get("token") or "")
    if not token:
        raise ValidationError("GitHub did not issue installation token")
    return token


def list_repositories(installation_id):
    token = installation_token(installation_id)
    response = httpx.get(
        f"{GITHUB_API}/installation/repositories",
        headers=_headers(token),
        params={"per_page": 100},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json().get("repositories", [])


def _safe_path(path):
    value = str(path or "").strip().lstrip("/")
    if not value or len(value) > 1024 or "\x00" in value:
        raise ValidationError("Invalid repository path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValidationError("Unsafe repository path")
    return value


def read_repository_file(binding, path, *, ref=None):
    path = _safe_path(path)
    token = installation_token(
        binding.installation.installation_id,
        repository_ids=[binding.repository_id],
        permissions={"contents": "read"},
    )
    response = httpx.get(
        f"{GITHUB_API}/repos/{binding.full_name}/contents/{quote(path, safe='/')}",
        headers=_headers(token),
        params={"ref": ref or binding.default_branch},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("type") != "file":
        raise ValidationError("Path is not a file")
    if payload.get("encoding") != "base64":
        raise ValidationError("Unsupported GitHub content encoding")
    raw = base64.b64decode(payload.get("content") or "", validate=False)
    if len(raw) > int(os.getenv("GITHUB_MAX_FILE_BYTES", str(2 * 1024 * 1024))):
        raise ValidationError("GitHub file exceeds configured size limit")
    if b"\x00" in raw:
        raise ValidationError("Binary files are not available in AI workspace")
    return {
        "path": path,
        "sha": payload.get("sha"),
        "size": len(raw),
        "content": raw.decode("utf-8"),
        "ref": ref or binding.default_branch,
    }


def write_repository_file(binding, path, *, content, expected_sha, message, branch=None):
    if not binding.write_enabled:
        raise ValidationError("GitHub write access is disabled for this project")
    path = _safe_path(path)
    raw = str(content).encode("utf-8")
    if len(raw) > int(os.getenv("GITHUB_MAX_WRITE_BYTES", str(1024 * 1024))):
        raise ValidationError("GitHub write exceeds configured size limit")
    if not expected_sha or len(expected_sha) > 64:
        raise ValidationError("Expected file SHA is required for safe update")
    commit_message = str(message or "AI Workspace update").strip()[:240]
    target_branch = str(branch or binding.default_branch).strip()
    if not target_branch or len(target_branch) > 255:
        raise ValidationError("Invalid branch")
    token = installation_token(
        binding.installation.installation_id,
        repository_ids=[binding.repository_id],
        permissions={"contents": "write"},
    )
    response = httpx.put(
        f"{GITHUB_API}/repos/{binding.full_name}/contents/{quote(path, safe='/')}",
        headers=_headers(token),
        json={
            "message": commit_message,
            "content": base64.b64encode(raw).decode("ascii"),
            "sha": expected_sha,
            "branch": target_branch,
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "path": path,
        "branch": target_branch,
        "content_sha": (payload.get("content") or {}).get("sha"),
        "commit_sha": (payload.get("commit") or {}).get("sha"),
    }
