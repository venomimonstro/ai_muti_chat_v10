import json
import os
import resource
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath


PORT = int(os.getenv("SANDBOX_PORT", "8090"))
SHARED_SECRET = os.getenv("SANDBOX_SHARED_SECRET", "").strip()
MAX_BODY_BYTES = int(os.getenv("SANDBOX_MAX_BODY_BYTES", str(2 * 1024 * 1024)))
MAX_FILES = int(os.getenv("SANDBOX_MAX_FILES", "300"))
MAX_FILE_BYTES = int(os.getenv("SANDBOX_MAX_FILE_BYTES", str(512 * 1024)))
MAX_OUTPUT_CHARS = int(os.getenv("SANDBOX_MAX_OUTPUT_CHARS", "30000"))
TIMEOUT_SECONDS = int(os.getenv("SANDBOX_TIMEOUT_SECONDS", "90"))

COMMANDS = {
    "python-compile": ["python", "-m", "compileall", "-q", "."],
    "pytest": ["python", "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1"],
    "django-check": ["python", "manage.py", "check"],
}


def _limits():
    cpu = max(1, min(TIMEOUT_SECONDS, 120))
    memory = 384 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
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


def _clean_env():
    return {
        "PATH": os.getenv("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": "/workspace",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def execute(payload):
    command_name = str(payload.get("command") or "").strip()
    command = COMMANDS.get(command_name)
    if not command:
        return {"ok": False, "error": "command_not_allowed"}, 400
    workspace_root = Path("/workspace")
    workspace_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=workspace_root) as temp:
        root = Path(temp)
        try:
            _write_files(root, payload.get("files") or [])
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}, 400
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
            return {
                "ok": completed.returncode == 0,
                "command": command_name,
                "returncode": completed.returncode,
                "output": output,
            }, 200
        except subprocess.TimeoutExpired as exc:
            output = str(exc.stdout or "")[-MAX_OUTPUT_CHARS:]
            return {"ok": False, "command": command_name, "error": "timeout", "output": output}, 408
        finally:
            for child in root.iterdir() if root.exists() else []:
                if child.is_symlink():
                    child.unlink(missing_ok=True)
            shutil.rmtree(root, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "AIWorkspaceSandbox/1.0"

    def _json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "configured": bool(SHARED_SECRET)})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/run":
            self._json(404, {"error": "not_found"})
            return
        if not SHARED_SECRET:
            self._json(503, {"error": "sandbox_not_configured"})
            return
        if self.headers.get("X-Sandbox-Token", "") != SHARED_SECRET:
            self._json(403, {"error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid_content_length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._json(413, {"error": "payload_too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(400, {"error": "invalid_json"})
            return
        result, status = execute(payload if isinstance(payload, dict) else {})
        self._json(status, result)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
