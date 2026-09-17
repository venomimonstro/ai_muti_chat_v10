import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

TOTP_PERIOD = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
SESSION_MFA_KEY = "platform_mfa_verified_at"
SESSION_MFA_MAX_AGE = 12 * 60 * 60


def _fernet():
    digest = hashlib.sha256(("mfa-v1:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(payload: str) -> str:
    try:
        return _fernet().decrypt(payload.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("MFA secret cannot be decrypted") from exc


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _decode_secret(secret: str) -> bytes:
    padding = "=" * ((8 - len(secret) % 8) % 8)
    return base64.b32decode((secret + padding).upper())


def totp_code(secret: str, at_time: int | None = None) -> str:
    counter = int((at_time or int(time.time())) // TOTP_PERIOD)
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**TOTP_DIGITS)
    return str(value).zfill(TOTP_DIGITS)


def verify_totp(secret: str, code: str, at_time: int | None = None) -> bool:
    if not code.isdigit() or len(code) != TOTP_DIGITS:
        return False
    now = at_time or int(time.time())
    return any(
        hmac.compare_digest(totp_code(secret, now + offset * TOTP_PERIOD), code)
        for offset in range(-TOTP_WINDOW, TOTP_WINDOW + 1)
    )


def recovery_hash(code: str) -> str:
    value = code.strip().upper().replace("-", "")
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def new_recovery_codes(count: int = 8) -> list[str]:
    return [f"{secrets.token_hex(4).upper()}-{secrets.token_hex(4).upper()}" for _ in range(count)]


def consume_recovery_code(profile, code: str) -> bool:
    digest = recovery_hash(code)
    hashes = list(profile.recovery_code_hashes or [])
    for index, item in enumerate(hashes):
        if hmac.compare_digest(item, digest):
            hashes.pop(index)
            profile.recovery_code_hashes = hashes
            profile.save(update_fields=["recovery_code_hashes", "updated_at"])
            return True
    return False


def otpauth_uri(*, secret: str, email: str) -> str:
    issuer = getattr(settings, "MFA_ISSUER", "AI Workspace")
    label = quote(f"{issuer}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={TOTP_DIGITS}&period={TOTP_PERIOD}"


def mark_session_verified(request):
    request.session[SESSION_MFA_KEY] = int(time.time())
    request.session.modified = True


def session_verified(request) -> bool:
    try:
        verified_at = int(request.session.get(SESSION_MFA_KEY, 0))
    except (TypeError, ValueError):
        return False
    return bool(verified_at and int(time.time()) - verified_at <= SESSION_MFA_MAX_AGE)
