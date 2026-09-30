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
                    "content": "OPENAI_API_KEY='" + "sk-" + "proj-" + ("a" * 32) + "'\n",
                }
            ]
        )


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("ANTHROPIC_API_KEY='" + "sk-" + "ant-" + ("a" * 32) + "'\n", "anthropic_key"),
        ("STRIPE_KEY='" + "sk_" + "live_" + ("a" * 32) + "'\n", "stripe_live_key"),
        ("SLACK_TOKEN='" + "xox" + "b-" + ("1" * 10) + "-" + ("a" * 26) + "'\n", "slack_token"),
        ("GOOGLE_API_KEY='" + "AI" + "za" + ("a" * 35) + "'\n", "google_api_key"),
    ],
)
def test_common_production_tokens_are_blocked(content, code):
    with pytest.raises(ValidationError, match=code):
        secure_change_set(
            [
                {
                    "path": "config/runtime.txt",
                    "operation": "update",
                    "content": content,
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
    assert "deployment" in classify_change_risk("backend/Dockerfile.sandbox", "update")
    assert "deployment" in classify_change_risk("infra/main.tf", "update")
    assert "deployment" in classify_change_risk(".github/workflows/release.yml", "update")
    assert "destructive_delete" in classify_change_risk("backend/legacy.py", "delete")
