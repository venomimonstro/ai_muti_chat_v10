from pathlib import Path
from unittest.mock import patch

import pytest

from . import sandbox_server


def test_sandbox_rejects_unknown_command():
    payload, status = sandbox_server.execute({"command": "bash", "files": []})
    assert status == 400
    assert payload["error"] == "command_not_allowed"


def test_sandbox_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_server, "MAX_BODY_BYTES", 1024 * 1024)
    root = Path(tmp_path)
    with pytest.raises(ValueError, match="unsafe_path"):
        sandbox_server._write_files(root, [{"path": "../escape.py", "content": "print(1)"}])


def test_sandbox_writes_only_relative_text_files(tmp_path):
    root = Path(tmp_path)
    sandbox_server._write_files(
        root,
        [
            {"path": "pkg/example.py", "content": "VALUE = 1\n"},
            {"path": "tests/test_example.py", "content": "def test_ok(): assert True\n"},
        ],
    )
    assert (root / "pkg/example.py").read_text() == "VALUE = 1\n"
    assert (root / "tests/test_example.py").exists()


def test_sandbox_python_compile_executes_without_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox_server, "TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(sandbox_server.tempfile, "TemporaryDirectory", lambda prefix, dir: _TempDir(tmp_path))
    payload, status = sandbox_server.execute(
        {"command": "python-compile", "files": [{"path": "ok.py", "content": "x = 1\n"}]}
    )
    assert status == 200
    assert payload["ok"] is True
    assert payload["returncode"] == 0


def test_workspace_rejects_unsafe_identifier():
    with pytest.raises(ValueError, match="invalid_workspace_id"):
        sandbox_server._workspace_path("../../root")


def test_workspace_supports_bounded_edit_lifecycle(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    monkeypatch.setattr(sandbox_server, "SESSION_ROOT", sessions)

    payload, status = sandbox_server._workspace_sync(
        {
            "workspace_id": "dev-run-12345678",
            "reset": True,
            "files": [
                {"path": "src/a.py", "content": "VALUE = 1\n"},
                {"path": "src/remove.py", "content": "OLD = True\n"},
            ],
        }
    )
    assert status == 200
    assert payload["files"] == 2

    payload, status = sandbox_server._workspace_patch(
        {
            "workspace_id": "dev-run-12345678",
            "operations": [
                {"operation": "update", "path": "src/a.py", "content": "VALUE = 2\n"},
                {"operation": "create", "path": "src/new.py", "content": "NEW = True\n"},
                {"operation": "move", "path": "src/new.py", "destination": "src/moved.py"},
                {"operation": "delete", "path": "src/remove.py"},
            ],
        }
    )
    assert status == 200
    root = sessions / "dev-run-12345678"
    assert (root / "src/a.py").read_text() == "VALUE = 2\n"
    assert (root / "src/moved.py").read_text() == "NEW = True\n"
    assert not (root / "src/remove.py").exists()

    payload, status = sandbox_server._workspace_destroy({"workspace_id": "dev-run-12345678"})
    assert status == 200
    assert payload["ok"] is True
    assert not root.exists()


def test_workspace_checks_stop_on_first_failure(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    root = sessions / "dev-run-87654321"
    root.mkdir(parents=True)
    monkeypatch.setattr(sandbox_server, "SESSION_ROOT", sessions)

    with patch.object(
        sandbox_server,
        "_run_command",
        side_effect=[
            {"ok": True, "command": "json-check", "returncode": 0, "output": "ok"},
            {"ok": False, "command": "npm-lint", "returncode": 1, "output": "bad"},
        ],
    ) as runner:
        payload, status = sandbox_server._workspace_run(
            {
                "workspace_id": "dev-run-87654321",
                "checks": ["json-check", "npm-lint", "npm-build"],
            }
        )

    assert status == 200
    assert payload["ok"] is False
    assert [item["command"] for item in payload["checks"]] == ["json-check", "npm-lint"]
    assert runner.call_count == 2


def test_workspace_rejects_arbitrary_command(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    root = sessions / "dev-run-abcdefgh"
    root.mkdir(parents=True)
    monkeypatch.setattr(sandbox_server, "SESSION_ROOT", sessions)

    payload, status = sandbox_server._workspace_run(
        {"workspace_id": "dev-run-abcdefgh", "checks": ["bash"]}
    )
    assert status == 200
    assert payload["ok"] is False
    assert payload["checks"][0]["error"] == "command_not_allowed"


class _TempDir:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        return False
