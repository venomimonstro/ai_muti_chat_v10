import re
from pathlib import Path


BACKEND_APPS = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGES = (
    BACKEND_APPS / "chat",
    BACKEND_APPS / "ai_registry",
)
UNSAFE_MARKER_GUARD = re.compile(
    r"if\s+(?:not\s+)?getattr\([^\n]*[\"']_ai_workspace_[^\"']+[\"']\s*,\s*False\s*\)\s*:"
)


def test_runtime_installers_use_exact_boolean_markers():
    """Proxy/Mock attributes must never look like an already-installed guard.

    Runtime safety is assembled during AppConfig.ready() from small idempotent
    wrappers. A plain truthiness check is unsafe because proxy objects such as Mock
    can synthesize arbitrary attributes. Installers must therefore compare marker
    attributes explicitly with True (or is not True for the inverse condition).
    """
    violations = []
    for package in RUNTIME_PACKAGES:
        for path in package.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            source = path.read_text(encoding="utf-8")
            for match in UNSAFE_MARKER_GUARD.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(BACKEND_APPS.parent)}:{line}: {match.group(0)}")
    assert violations == [], "Unsafe runtime installer marker guards:\n" + "\n".join(violations)
