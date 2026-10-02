from pathlib import Path


APPS_DIR = Path(__file__).resolve().parents[1]

COMMERCIAL_RUNTIME_FILES = (
    "b2b_api/services.py",
    "chat/compare.py",
    "image_studio/services.py",
    "agents/runtime.py",
    "agents/generic_team_runtime.py",
    "agents/graph_runtime.py",
    "agents/ai_planner_views.py",
    "agents/dev_model_execution.py",
)


def _source(relative_path: str) -> str:
    return (APPS_DIR / relative_path).read_text(encoding="utf-8")


def test_commercial_runtime_never_imports_legacy_adapter_dispatch():
    violations = []
    for relative_path in COMMERCIAL_RUNTIME_FILES:
        source = _source(relative_path)
        for line_number, line in enumerate(source.splitlines(), start=1):
            compact = line.strip()
            if (
                compact.startswith("from apps.ai_registry.adapters import")
                and "adapter_for" in compact
            ):
                violations.append(
                    f"{relative_path}:{line_number}: legacy adapters.adapter_for import"
                )
            if compact.startswith("from apps.ai_registry.dispatch import adapter_for"):
                violations.append(
                    f"{relative_path}:{line_number}: stale-prone adapter_for value import"
                )
            if ".get_api_key()" in compact:
                violations.append(
                    f"{relative_path}:{line_number}: direct provider.get_api_key() bypass"
                )
    assert violations == [], "Unsafe commercial credential dispatch:\n" + "\n".join(violations)


def test_paid_runtime_binds_execution_to_reserved_funding_account():
    """Every paid execution path must pin provider execution to its ledger account."""
    required_binding = {
        "b2b_api/services.py": "funding_account_id=funding_account_id",
        "chat/compare.py": "funding_account_id=funding_account_id",
        "agents/runtime.py": "funding_account_id=(",
        "agents/generic_team_runtime.py": "funding_account_id=(",
        "agents/graph_runtime.py": "funding_account_id=(",
        "agents/ai_planner_views.py": "funding_account_id=(",
        "agents/dev_model_execution.py": "funding_account_id=funding_account_id",
    }
    missing = [
        f"{relative_path}: missing {needle}"
        for relative_path, needle in required_binding.items()
        if needle not in _source(relative_path)
    ]
    assert missing == [], "Commercial runtime/account binding regression:\n" + "\n".join(missing)
