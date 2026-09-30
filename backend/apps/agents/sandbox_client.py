import os

import httpx
from django.core.exceptions import ValidationError


class SandboxUnavailable(ValidationError):
    pass


def sandbox_enabled():
    return bool(os.getenv("SANDBOX_SHARED_SECRET", "").strip())


def _base_url():
    return os.getenv("SANDBOX_URL", "http://sandbox:8090").rstrip("/")


def sandbox_health(*, timeout=2.5):
    if not sandbox_enabled():
        return {"healthy": False, "configured": False, "workspace_api": False, "error": "sandbox_not_configured"}
    try:
        response = httpx.get(f"{_base_url()}/health", timeout=float(timeout))
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"healthy": False, "configured": True, "workspace_api": False, "error": exc.__class__.__name__}
    healthy = response.status_code == 200 and data.get("status") == "ok" and bool(data.get("configured"))
    return {
        "healthy": healthy,
        "configured": bool(data.get("configured")),
        "workspace_api": bool(data.get("workspace_api")),
        "commands": list(data.get("commands") or []),
        "error": "" if healthy else str(data.get("error") or f"http_{response.status_code}"),
    }


def _request(path, payload):
    secret = os.getenv("SANDBOX_SHARED_SECRET", "").strip()
    if not secret:
        raise SandboxUnavailable("Sandbox не настроен: задайте SANDBOX_SHARED_SECRET")
    timeout = float(os.getenv("SANDBOX_CLIENT_TIMEOUT_SECONDS", "140"))
    try:
        response = httpx.post(
            f"{_base_url()}{path}",
            headers={"X-Sandbox-Token": secret},
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise SandboxUnavailable("Sandbox временно недоступен") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise SandboxUnavailable("Sandbox вернул некорректный ответ") from exc
    if response.status_code >= 400:
        raise SandboxUnavailable(str(data.get("error") or "sandbox_error"))
    return data


def run_sandbox(*, command, files):
    return _request("/run", {"command": command, "files": files})


def sync_workspace(*, workspace_id, files, reset=True):
    return _request(
        "/workspace/sync",
        {"workspace_id": str(workspace_id), "files": files, "reset": bool(reset)},
    )


def patch_workspace(*, workspace_id, operations):
    return _request(
        "/workspace/patch",
        {"workspace_id": str(workspace_id), "operations": operations},
    )


def run_workspace_checks(*, workspace_id, checks):
    return _request(
        "/workspace/run",
        {"workspace_id": str(workspace_id), "checks": list(checks)},
    )


def destroy_workspace(*, workspace_id):
    return _request("/workspace/destroy", {"workspace_id": str(workspace_id)})
