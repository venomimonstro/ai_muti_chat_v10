import json
import os
import re
import resource
import shutil
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath


PORT = int(os.getenv("SANDBOX_PORT", "8090"))
SHARED_SECRET = os.getenv("SANDBOX_SHARED_SECRET", "").strip()
MAX_BODY_BYTES = int(os.getenv("SANDBOX_MAX_BODY_BYTES", str(8 * 1024 * 1024)))
MAX_FILES = int(os.getenv("SANDBOX_MAX_FILES", "1000"))
MAX_FILE_BYTES = int(os.getenv("SANDBOX_MAX_FILE_BYTES", str(1024 * 1024)))
MAX_OUTPUT_CHARS = int(os.getenv("SANDBOX_MAX_OUTPUT_CHARS", "30000"))
TIMEOUT_SECONDS = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "120"))
WORKSPACE_TTL_SECONDS = int(os.getenv("SANDBOX_WORKSPACE_TTL_SECONDS", "3600"))
MAX_CHECKS = int(os.getenv("SANDBOX_MAX_CHECKS", "8"))
WORKSPACE_ROOT = Path("/workspace")
SESSION_ROOT = WORKSPACE_ROOT / "sessions"
WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,96}$")

COMMANDS = {
    "python-compile": ["python", "-m", "compileall", "-q", "."],
    "pytest": ["python", "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1"],
    "pytest-backend": ["python", "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", "backend"],
    "django-check": ["python", "manage.py", "check"],
    "django-check-backend": ["python", "backend/manage.py", "check"],
    "npm-test": ["npm", "test", "--if-present"],
    "npm-build": ["npm", "run", "build", "--if-present"],
    "npm-lint": ["npm", "run", "lint", "--if-present"],
}

NODE_CHECK_SCRIPT = r'''
import pathlib, subprocess, sys
suffixes = {".js", ".mjs", ".cjs"}
files = [p for p in pathlib.Path(".").rglob("*") if p.is_file() and p.suffix.lower() in suffixes]
for path in files:
    result = subprocess.run(["node", "--check", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        print(result.stdout)
        sys.exit(result.returncode)
print(f"node syntax ok: {len(files)} files")
'''.strip()

JSON_CHECK_SCRIPT = r'''
import json, pathlib, sys
files = [p for p in pathlib.Path(".").rglob("*.json") if p.is_file()]
for path in files:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"{path}: {exc}")
        sys.exit(1)
print(f"json syntax ok: {len(files)} files")
'''.strip()


def _limits():
    cpu = max(1, min(TIMEOUT_SECONDS, 180))
    memory = 768 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (96, 96))
    except (ValueError, OSError):
        pass


def _safe_relative_path(value):
    raw = str(value or "").replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(raw)
    if (
        not raw
        or len(raw) > 500
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.startswith(".git") for part in path.parts)
    ):
        raise ValueError("unsafe_path")
    return path


def _safe_workspace_id(value):
    workspace_id = str(value or "").strip()
    if not WORKSPACE_ID_RE.fullmatch(workspace_id):
        raise ValueError("invalid_workspace_id")
    return workspace_id


def _workspace_path(workspace_id):
    return SESSION_ROOT / _safe_workspace_id(workspace_id)


def _write_files(root, files):
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError("too_many_files")
    total = 0
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("invalid_file")
        rel = _safe_relative_path(item.get("path"))
        content = item.get("content")
        if not isinstance(content, str):
            raise ValueError("text_files_only")
        raw = content.encode("utf-8")
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("file_too_large")
        total += len(raw)
        if total > MAX_BODY_BYTES:
            raise ValueError("workspace_too_large")
        target = root.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return {"files": len(files), "bytes": total}


def _apply_operations(root, operations):
    if not isinstance(operations, list) or len(operations) > MAX_FILES:
        raise ValueError("too_many_operations")
    applied = []
    for item in operations:
        if not isinstance(item, dict):
            raise ValueError("invalid_operation")
        operation = str(item.get("operation") or "update").strip().lower()
        rel = _safe_relative_path(item.get("path"))
        target = root.joinpath(*rel.parts)
        if operation in {"create", "update"}:
            content = item.get("content")
            if not isinstance(content, str):
                raise ValueError("text_files_only")
            raw = content.encode("utf-8")
            if len(raw) > MAX_FILE_BYTES:
                raise ValueError("file_too_large")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        elif operation == "delete":
            if target.exists() and target.is_file() and not target.is_symlink():
                target.unlink()
        elif operation == "move":
            destination = _safe_relative_path(item.get("destination"))
            destination_target = root.joinpath(*destination.parts)
            if not target.exists() or not target.is_file() or target.is_symlink():
                raise ValueError("move_source_missing")
            destination_target.parent.mkdir(parents=True, exist_ok=True)
            if destination_target.exists():
                raise ValueError("move_destination_exists")
            target.replace(destination_target)
        else:
            raise ValueError("operation_not_allowed")
        applied.append({"operation": operation, "path": str(rel)})
    return applied


def _clean_env():
    return {
        "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/workspace",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "CI": "1",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_AUDIT": "false",
    }


def _command(command_name):
    if command_name == "node-check":
        return ["python", "-c", NODE_CHECK_SCRIPT]
    if command_name == "json-check":
        return ["python", "-c", JSON_CHECK_SCRIPT]
    return COMMANDS.get(command_name)


def _run_command(root, command_name):
    command = _command(command_name)
    if not command:
        return {"ok": False, "command": command_name, "error": "command_not_allowed"}
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=_clean_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
            shell=False,
            preexec_fn=_limits,
        )
        output = (completed.stdout or "")[-MAX_OUTPUT_CHARS:]
        return {"ok": completed.returncode == 0, "command": command_name, "returncode": completed.returncode, "output": output}
    except subprocess.TimeoutExpired as exc:
        output = str(exc.stdout or "")[-MAX_OUTPUT_CHARS:]
        return {"ok": False, "command": command_name, "error": "timeout", "output": output}
    except FileNotFoundError:
        return {"ok": False, "command": command_name, "error": "runtime_not_installed", "output": ""}


def _run_checks(root, checks):
    if not isinstance(checks, list) or not checks or len(checks) > MAX_CHECKS:
        raise ValueError("invalid_checks")
    results = []
    for raw in checks:
        result = _run_command(root, str(raw or "").strip())
        results.append(result)
        if not result.get("ok"):
            break
    return {"ok": bool(results) and all(item.get("ok") for item in results), "checks": results}


def _cleanup_stale_workspaces():
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - max(300, WORKSPACE_TTL_SECONDS)
    for child in SESSION_ROOT.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def execute(payload):
    command_name = str(payload.get("command") or "").strip()
    if not _command(command_name):
        return {"ok": False, "error": "command_not_allowed"}, 400
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=WORKSPACE_ROOT) as temp:
        root = Path(temp)
        try:
            _write_files(root, payload.get("files") or [])
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}, 400
        result = _run_command(root, command_name)
        return result, 408 if result.get("error") == "timeout" else 200


def _workspace_sync(payload):
    root = _workspace_path(payload.get("workspace_id"))
    if bool(payload.get("reset", True)) and root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    stats = _write_files(root, payload.get("files") or [])
    os.utime(root, None)
    return {"ok": True, "workspace_id": root.name, **stats}, 200


def _workspace_patch(payload):
    root = _workspace_path(payload.get("workspace_id"))
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": "workspace_not_found"}, 404
    applied = _apply_operations(root, payload.get("operations") or [])
    os.utime(root, None)
    return {"ok": True, "workspace_id": root.name, "applied": applied}, 200


def _workspace_run(payload):
    root = _workspace_path(payload.get("workspace_id"))
    if not root.exists() or not root.is_dir():
        return {"ok": False, "error": "workspace_not_found"}, 404
    result = _run_checks(root, payload.get("checks") or [])
    os.utime(root, None)
    return {"workspace_id": root.name, **result}, 200


def _workspace_destroy(payload):
    root = _workspace_path(payload.get("workspace_id"))
    if root.exists() and root.is_dir() and not root.is_symlink():
        shutil.rmtree(root, ignore_errors=True)
    return {"ok": True, "workspace_id": root.name}, 200


class Handler(BaseHTTPRequestHandler):
    server_version = "AIWorkspaceSandbox/2.1"

    def _json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self):
        return bool(SHARED_SECRET) and self.headers.get("X-Sandbox-Token", "") == SHARED_SECRET

    def _payload(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid_content_length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("payload_too_large")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("invalid_json") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid_json")
        return payload

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "configured": bool(SHARED_SECRET), "workspace_api": True, "commands": sorted([*COMMANDS, "node-check", "json-check"])})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if not self._authorized():
            self._json(503 if not SHARED_SECRET else 403, {"error": "sandbox_not_configured" if not SHARED_SECRET else "forbidden"})
            return
        try:
            payload = self._payload()
            _cleanup_stale_workspaces()
            if self.path == "/run":
                result, status = execute(payload)
            elif self.path == "/workspace/sync":
                result, status = _workspace_sync(payload)
            elif self.path == "/workspace/patch":
                result, status = _workspace_patch(payload)
            elif self.path == "/workspace/run":
                result, status = _workspace_run(payload)
            elif self.path == "/workspace/destroy":
                result, status = _workspace_destroy(payload)
            else:
                result, status = {"error": "not_found"}, 404
        except ValueError as exc:
            result, status = {"ok": False, "error": str(exc)}, 400
        except OSError:
            result, status = {"ok": False, "error": "workspace_io_error"}, 500
        self._json(status, result)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
