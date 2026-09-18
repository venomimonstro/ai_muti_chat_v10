import pytest


@pytest.fixture(autouse=True)
def _github_integration_enabled(monkeypatch):
    monkeypatch.setenv("GITHUB_INTEGRATION_ENABLED", "true")
