import os

import httpx
from django.core.exceptions import ValidationError


class SandboxUnavailable(ValidationError):
    pass


def sandbox_enabled():
    return bool(os.getenv("SANDBOX_SHARED_SECRET", "").strip())


def run_sandbox(*, command, files):
    secret = os.getenv("SANDBOX_SHARED_SECRET", "").strip()
    if not secret:
        raise SandboxUnavailable("Sandbox не настроен: задайте SANDBOX_SHARED_SECRET")
    url = os.getenv("SANDBOX_URL", "http://sandbox:8090").rstrip("/")
    timeout = float(os.getenv("SANDBOX_CLIENT_TIMEOUT_SECONDS", "100"))
    try:
        response = httpx.post(
            f"{url}/run",
            headers={"X-Sandbox-Token": secret},
            json={"command": command, "files": files},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise SandboxUnavailable("Sandbox временно недоступен") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise SandboxUnavailable("Sandbox вернул некорректный ответ") from exc
    if response.status_code >= 400:
        raise SandboxUnavailable(str(payload.get("error") or "sandbox_error"))
    return payload
