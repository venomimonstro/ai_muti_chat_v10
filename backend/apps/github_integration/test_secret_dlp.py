import pytest
from django.core.exceptions import ValidationError

from .services import _assert_no_high_risk_secret


def _samples():
    return [
        "-----BEGIN " + "PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        "github_" + "pat_" + ("a" * 40),
        "gh" + "p_" + ("a" * 40),
        "sk-" + "proj-" + ("a" * 40),
        "AKIA" + ("A" * 16),
        "xox" + "b-" + ("1" * 12) + "-" + ("2" * 12) + "-" + ("a" * 24),
        "sk_" + "live_" + ("a" * 26),
    ]


@pytest.mark.parametrize("secret", _samples())
def test_high_risk_secret_patterns_are_blocked(secret, monkeypatch):
    monkeypatch.delenv("GITHUB_ALLOW_SECRET_CONTENT", raising=False)

    with pytest.raises(ValidationError, match="похожие на действующий секрет"):
        _assert_no_high_risk_secret(f"const token = '{secret}'")


def test_normal_source_code_is_not_blocked(monkeypatch):
    monkeypatch.delenv("GITHUB_ALLOW_SECRET_CONTENT", raising=False)
    _assert_no_high_risk_secret("def hello():\n    return 'world'\n")
