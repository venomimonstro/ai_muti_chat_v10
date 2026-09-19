import base64
import os

from django.core.exceptions import ValidationError

from .services import (
    _assert_no_high_risk_secret,
    _contents_url,
    _headers,
    _json_request,
    _safe_path,
    installation_token,
)


def _write_token(binding):
    if not binding.write_enabled:
        raise ValidationError("GitHub write access is disabled for this project")
    permissions = binding.installation.permissions or {}
    if permissions.get("contents") != "write":
        raise ValidationError("GitHub App не имеет разрешения contents: write")
    return installation_token(
        binding.installation.installation_id,
        repository_ids=[binding.repository_id],
        permissions={"contents": "write"},
    )


def _branch(binding, branch=None):
    value = str(branch or binding.default_branch).strip()
    if not value or len(value) > 255 or "\x00" in value:
        raise ValidationError("Invalid branch")
    return value


def create_repository_file(binding, path, *, content, message, branch=None):
    path = _safe_path(path)
    text = str(content)
    _assert_no_high_risk_secret(text)
    raw = text.encode("utf-8")
    if len(raw) > int(os.getenv("GITHUB_MAX_WRITE_BYTES", str(1024 * 1024))):
        raise ValidationError("GitHub write exceeds configured size limit")
    target_branch = _branch(binding, branch)
    token = _write_token(binding)
    payload = _json_request(
        "PUT",
        _contents_url(binding, path),
        headers=_headers(token),
        json={
            "message": str(message or "AI Workspace create file").strip()[:240],
            "content": base64.b64encode(raw).decode("ascii"),
            "branch": target_branch,
        },
    )
    return {
        "path": path,
        "branch": target_branch,
        "content_sha": (payload.get("content") or {}).get("sha"),
        "commit_sha": (payload.get("commit") or {}).get("sha"),
    }


def delete_repository_file(binding, path, *, expected_sha, message, branch=None):
    path = _safe_path(path)
    expected_sha = str(expected_sha or "").strip()
    if not expected_sha or len(expected_sha) > 64:
        raise ValidationError("Expected file SHA is required for safe delete")
    target_branch = _branch(binding, branch)
    token = _write_token(binding)
    payload = _json_request(
        "DELETE",
        _contents_url(binding, path),
        headers=_headers(token),
        json={
            "message": str(message or "AI Workspace delete file").strip()[:240],
            "sha": expected_sha,
            "branch": target_branch,
        },
    )
    return {
        "path": path,
        "branch": target_branch,
        "commit_sha": (payload.get("commit") or {}).get("sha"),
    }
