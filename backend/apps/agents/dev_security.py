import re
from pathlib import PurePosixPath

from django.core.exceptions import ValidationError


BLOCKED_SECRET_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"}
SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
)
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?im)^\s*[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY)[A-Z0-9_]*\s*=\s*['\"]?([^'\"\s#]{8,})"
)
PLACEHOLDER_MARKERS = ("example", "sample", "dummy", "test", "changeme", "change-me", "replace-me", "placeholder")
DEPENDENCY_FILES = {
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "composer.json",
    "composer.lock",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "cargo.lock",
}
DEPLOYMENT_NAMES = {
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.prod.yml",
    "docker-compose.prod.yaml",
    "compose.yml",
    "compose.yaml",
    "caddyfile",
    "nginx.conf",
}


def _secret_finding(path, content):
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix in BLOCKED_SECRET_SUFFIXES:
        return "secret_file_extension"
    for code, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            return code
    for match in SENSITIVE_ASSIGNMENT.finditer(content):
        value = match.group(1).casefold()
        if not any(marker in value for marker in PLACEHOLDER_MARKERS):
            return "sensitive_assignment"
    return ""


def classify_change_risk(path, operation):
    normalized = str(path or "").replace("\\", "/").strip().casefold()
    name = normalized.rsplit("/", 1)[-1]
    risks = []
    if name in DEPENDENCY_FILES:
        risks.append("dependency_manifest")
    if name in DEPLOYMENT_NAMES or normalized.startswith(("deploy/", "infra/", "ops/")):
        risks.append("deployment")
    if "/migrations/" in f"/{normalized}" or name.startswith("migration"):
        risks.append("database_migration")
    if any(token in normalized for token in ("auth", "security", "permission", "billing", "payment", "wallet", "secrets")):
        risks.append("security_or_money_sensitive")
    if operation == "delete":
        risks.append("destructive_delete")
    return sorted(set(risks))


def secure_change(change):
    item = dict(change)
    path = str(item.get("path") or "")
    operation = str(item.get("operation") or "update")
    if operation in {"create", "update"}:
        content = str(item.get("content") or "")
        finding = _secret_finding(path, content)
        if finding:
            raise ValidationError(
                f"Dev Studio заблокировал {path}: change-set содержит секрет или приватный ключ ({finding})"
            )
    item["risk_flags"] = classify_change_risk(path, operation)
    return item


def secure_change_set(changes):
    return [secure_change(item) for item in changes]
