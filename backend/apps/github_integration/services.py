import base64
import json
import os
import re
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
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{24,}\b"),
    re.compile(r"\bsk_live_[A-Za-z0-9]{20,}\b"),
)


def _env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} is not configured")
    return value


def integration_enabled():
    return os.getenv("GITHUB_INTEGRATION_ENABLED", "false").strip().lower() == "true"


def configured():
    return integration_enabled() and all(
        os.getenv(name, "").strip()
        for name in (
            "GITHUB_APP_ID",
            "GITHUB_APP_SLUG",
            "GITHUB_APP_PRIVATE_KEY",
            "GITHUB_APP_CLIENT_ID",
            "GITHUB_APP_CLIENT_SECRET",
        )
    )


def install_url(state):
    if not integration_enabled():
        raise ImproperlyConfigured("GitHub integration is disabled")
    slug = _env("GITHUB_APP_SLUG")
    return f"{GITHUB_WEB}/apps/{quote(slug, safe='')}/installations/new?state={quote(state, safe='')}"


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def app_jwt():
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"iat": now - 60, "exp": now + 540, "iss": _env("GITHUB_APP_ID")},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    try:
        private_key = serialization.load_pem_private_key(
            _env("GITHUB_APP_PRIVATE_KEY").replace("\\n", "\n").encode(),
            password=None,
        )
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured("GitHub App private key is invalid") from exc
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def _headers(token):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "AIWorkspace-GitHubApp/1.0",
    }


def _json_request(method, url, **kwargs):
    try:
        response = httpx.request(method, url, timeout=TIMEOUT, **kwargs)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValidationError("GitHub temporarily unavailable or returned an invalid response") from exc


def exchange_user_code(code):
    payload = _json_request(
        "POST",
        f"{GITHUB_WEB}/login/oauth/access_token",
        data={
            "client_id": _env("GITHUB_APP_CLIENT_ID"),
            "client_secret": _env("GITHUB_APP_CLIENT_SECRET"),
            "code": code,
        },
        headers={"Accept": "application/json", "User-Agent": "AIWorkspace-GitHubApp/1.0"},
    )
    token = str(payload.get("access_token") or "")
    if not token:
        raise ValidationError("GitHub authorization failed")
    return token


def user_installations(user_token):
    payload = _json_request(
        "GET",
        f"{GITHUB_API}/user/installations",
        headers=_headers(user_token),
        params={"per_page": 100},
    )
    installations = payload.get("installations", [])
    return installations if isinstance(installations, list) else []


def verified_installation(user_token, installation_id):
    for installation in user_installations(user_token):
        if int(installation.get("id", 0)) == int(installation_id):
            return installation
    raise ValidationError("GitHub installation is not owned by the authorized user")


def installation_token(installation_id, *, repository_ids=None, permissions=None):
    body = {}
    if repository_ids:
        body["repository_ids"] = [int(value) for value in repository_ids]
    if permissions:
        body["permissions"] = permissions
    payload = _json_request(
        "POST",
        f"{GITHUB_API}/app/installations/{int(installation_id)}/access_tokens",
        headers=_headers(app_jwt()),
        json=body,
    )
    token = str(payload.get("token") or "")
    if not token:
        raise ValidationError("GitHub did not issue installation token")
    return token


def list_repositories(installation_id):
    token = installation_token(installation_id)
    payload = _json_request(
        "GET",
        f"{GITHUB_API}/installation/repositories",
        headers=_headers(token),
        params={"per_page": 100},
    )
    return payload.get("repositories", [])


def _safe_path(path):
    value = str(path or "").strip().lstrip("/")
    if not value or len(value) > 1024 or "\x00" in value:
        raise ValidationError("Invalid repository path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValidationError("Unsafe repository path")
    return value


def _safe_directory_path(path):
    value = str(path or "").strip().lstrip("/")
    if not value:
        return ""
    return _safe_path(value)


def _assert_no_high_risk_secret(text):
    if os.getenv("GITHUB_ALLOW_SECRET_CONTENT", "false").strip().lower() == "true":
        return
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise ValidationError(
                "Файл содержит данные, похожие на действующий секрет или приватный ключ; "
                "передача AI заблокирована"
            )


def _contents_url(binding, path=""):
    suffix = f"/{quote(path, safe='/')}" if path else ""
    return f"{GITHUB_API}/repos/{binding.full_name}/contents{suffix}"


def _assert_regular_file(binding, path, *, ref, token):
    parent, _, basename = path.rpartition("/")
    payload = _json_request(
        "GET",
        _contents_url(binding, parent),
        headers=_headers(token),
        params={"ref": ref},
    )
    if not isinstance(payload, list):
        raise ValidationError("Repository parent path is not a directory")
    entry = next(
        (
            item
            for item in payload
            if str(item.get("name") or "") == basename
            and str(item.get("path") or "") == path
        ),
        None,
    )
    if entry is None:
        raise ValidationError("Repository file does not exist")
    if (
        entry.get("type") != "file"
        or entry.get("target")
        or entry.get("submodule_git_url")
    ):
        raise ValidationError("Symlinks and submodules are not available in AI workspace")
    return entry


def list_repository_directory(binding, path="", *, ref=None):
    path = _safe_directory_path(path)
    token = installation_token(
        binding.installation.installation_id,
        repository_ids=[binding.repository_id],
        permissions={"contents": "read"},
    )
    payload = _json_request(
        "GET",
        _contents_url(binding, path),
        headers=_headers(token),
        params={"ref": ref or binding.default_branch},
    )
    if not isinstance(payload, list):
        raise ValidationError("Path is not a directory")
    max_items = max(1, min(int(os.getenv("GITHUB_MAX_DIRECTORY_ITEMS", "1000")), 1000))
    if len(payload) > max_items:
        raise ValidationError("GitHub directory exceeds configured item limit")
    items = []
    for item in payload:
        item_type = item.get("type")
        if item_type not in {"file", "dir"}:
            continue
        if item.get("target") or item.get("submodule_git_url"):
            continue
        item_path = str(item.get("path") or "")
        if not item_path or len(item_path) > 1024:
            continue
        items.append(
            {
                "name": str(item.get("name") or "")[:255],
                "path": item_path,
                "type": item_type,
                "size": int(item.get("size") or 0),
                "sha": str(item.get("sha") or "")[:64],
            }
        )
    items.sort(key=lambda item: (item["type"] != "dir", item["name"].casefold()))
    return {"path": path, "ref": ref or binding.default_branch, "items": items}


def read_repository_file(binding, path, *, ref=None):
    path = _safe_path(path)
    target_ref = ref or binding.default_branch
    token = installation_token(
        binding.installation.installation_id,
        repository_ids=[binding.repository_id],
        permissions={"contents": "read"},
    )
    _assert_regular_file(binding, path, ref=target_ref, token=token)
    payload = _json_request(
        "GET",
        _contents_url(binding, path),
        headers=_headers(token),
        params={"ref": target_ref},
    )
    if not isinstance(payload, dict) or payload.get("type") != "file":
        raise ValidationError("Path is not a file")
    if payload.get("target") or payload.get("submodule_git_url"):
        raise ValidationError("Symlinks and submodules are not available in AI workspace")
    if payload.get("encoding") != "base64":
        raise ValidationError("Unsupported GitHub content encoding")
    try:
        raw = base64.b64decode(payload.get("content") or "", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValidationError("GitHub returned malformed file content") from exc
    if len(raw) > int(os.getenv("GITHUB_MAX_FILE_BYTES", str(2 * 1024 * 1024))):
        raise ValidationError("GitHub file exceeds configured size limit")
    if b"\x00" in raw:
        raise ValidationError("Binary files are not available in AI workspace")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Only UTF-8 text files are available in AI workspace") from exc
    _assert_no_high_risk_secret(text)
    return {
        "path": path,
        "sha": payload.get("sha"),
        "size": len(raw),
        "content": text,
        "ref": target_ref,
    }


def write_repository_file(binding, path, *, content, expected_sha, message, branch=None):
    if not binding.write_enabled:
        raise ValidationError("GitHub write access is disabled for this project")
    path = _safe_path(path)
    text = str(content)
    _assert_no_high_risk_secret(text)
    raw = text.encode("utf-8")
    if len(raw) > int(os.getenv("GITHUB_MAX_WRITE_BYTES", str(1024 * 1024))):
        raise ValidationError("GitHub write exceeds configured size limit")
    if not expected_sha or len(expected_sha) > 64:
        raise ValidationError("Expected file SHA is required for safe update")
    commit_message = str(message or "AI Workspace update").strip()[:240]
    target_branch = str(branch or binding.default_branch).strip()
    if not target_branch or len(target_branch) > 255 or "\x00" in target_branch:
        raise ValidationError("Invalid branch")
    token = installation_token(
        binding.installation.installation_id,
        repository_ids=[binding.repository_id],
        permissions={"contents": "write"},
    )
    entry = _assert_regular_file(binding, path, ref=target_branch, token=token)
    current_sha = str(entry.get("sha") or "")
    if current_sha and current_sha != expected_sha:
        raise ValidationError("GitHub file changed after it was opened; reload before writing")
    payload = _json_request(
        "PUT",
        _contents_url(binding, path),
        headers=_headers(token),
        json={
            "message": commit_message,
            "content": base64.b64encode(raw).decode("ascii"),
            "sha": expected_sha,
            "branch": target_branch,
        },
    )
    return {
        "path": path,
        "branch": target_branch,
        "content_sha": (payload.get("content") or {}).get("sha"),
        "commit_sha": (payload.get("commit") or {}).get("sha"),
    }
