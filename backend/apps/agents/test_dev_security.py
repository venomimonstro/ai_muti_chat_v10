import pytest
from django.core.exceptions import ValidationError

from .dev_security import classify_change_risk, secure_change_set


def test_private_key_is_blocked_before_approval():
    with pytest.raises(ValidationError, match="private_key"):
        secure_change_set(
            [
                {
                    "path": "config/credentials.txt",
                    "operation": "create",
                    "content": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
                }
            ]
        )


def test_high_confidence_api_secret_is_blocked():
    with pytest.raises(ValidationError, match="openai_key"):
        secure_change_set(
            [
                {
                    "path": "config/settings.py",
                    "operation": "update",
                    "content": "OPENAI_API_KEY='sk-proj-abcdefghijklmnopqrstuvwxyz123456'\n",
                }
            ]
        )


def test_placeholder_secret_is_allowed_but_sensitive_path_is_flagged():
    changes = secure_change_set(
        [
            {
                "path": "backend/security/settings.py",
                "operation": "update",
                "content": "API_KEY='replace-me-example'\n",
            }
        ]
    )
    assert "security_or_money_sensitive" in changes[0]["risk_flags"]


def test_dependency_deployment_and_delete_risks_are_classified():
    assert classify_change_risk("package.json", "update") == ["dependency_manifest"]
    assert "deployment" in classify_change_risk("deploy/Caddyfile", "update")
    assert "destructive_delete" in classify_change_risk("backend/legacy.py", "delete")
