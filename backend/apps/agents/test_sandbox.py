from pathlib import Path

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


class _TempDir:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        return False
