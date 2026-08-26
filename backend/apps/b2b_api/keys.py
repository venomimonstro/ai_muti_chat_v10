import hashlib
import hmac
import ipaddress
import secrets

from django.conf import settings
from django.utils import timezone

from .models import APIKey, AuditLog

DEFAULT_SCOPES = ["chat.completions", "models.read", "usage.read"]


def _digest(raw_key: str) -> str:
    return hmac.new(
        settings.B2B_API_KEY_PEPPER.encode(), raw_key.encode(), hashlib.sha256
    ).hexdigest()


def issue_key(*, organization, actor, name, **options):
    raw_key = f"aw_live_{secrets.token_urlsafe(36)}"
    key = APIKey.objects.create(
        organization=organization,
        created_by=actor,
        name=name,
        prefix=raw_key[:20],
        secret_hash=_digest(raw_key),
        scopes=options.get("scopes") or DEFAULT_SCOPES,
        allowed_models=options.get("allowed_models") or [],
        allowed_endpoints=options.get("allowed_endpoints") or [],
        monthly_limit_rub=options.get("monthly_limit_rub"),
        rate_limit_per_minute=options.get("rate_limit_per_minute", 60),
        max_concurrency=options.get("max_concurrency", 2),
        ip_allowlist=options.get("ip_allowlist") or [],
        expires_at=options.get("expires_at"),
    )
    AuditLog.objects.create(
        organization=organization,
        actor=actor,
        action="api_key.created",
        target_id=str(key.id),
        metadata={"name": key.name, "prefix": key.prefix},
    )
    return key, raw_key


def revoke_key(key, *, actor):
    if key.revoked_at is None:
        key.revoked_at = timezone.now()
        key.save(update_fields=["revoked_at"])
        AuditLog.objects.create(
            organization=key.organization,
            actor=actor,
            action="api_key.revoked",
            target_id=str(key.id),
            metadata={"prefix": key.prefix},
        )
    return key


def key_is_active(key) -> bool:
    now = timezone.now()
    return bool(
        key.organization.active
        and key.revoked_at is None
        and (key.expires_at is None or key.expires_at > now)
    )


def authenticate_raw_key(raw_key: str):
    if not raw_key.startswith("aw_live_"):
        return None
    key = (
        APIKey.objects.select_related("organization", "organization__billing_user")
        .filter(prefix=raw_key[:20])
        .first()
    )
    if key is None or not hmac.compare_digest(key.secret_hash, _digest(raw_key)):
        return None
    return key if key_is_active(key) else None


def ip_is_allowed(key, remote_addr: str) -> bool:
    if not key.ip_allowlist:
        return True
    try:
        address = ipaddress.ip_address(remote_addr)
        return any(address in ipaddress.ip_network(item, strict=False) for item in key.ip_allowlist)
    except ValueError:
        return False
