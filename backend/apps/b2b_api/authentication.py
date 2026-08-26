import ipaddress

from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .keys import authenticate_raw_key, ip_is_allowed


def _peer_is_trusted_proxy(remote_addr: str) -> bool:
    if not remote_addr:
        return False
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    for entry in settings.B2B_TRUSTED_PROXY_IPS:
        if "/" in entry:
            try:
                if address in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
        elif remote_addr == entry:
            return True
    return False


def _client_ip(request):
    remote = request.META.get("REMOTE_ADDR", "")
    if settings.B2B_TRUST_PROXY_IP_HEADER and _peer_is_trusted_proxy(remote):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return remote


class APIKeyAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        if not settings.B2B_API_ENABLED:
            raise AuthenticationFailed("Public API is disabled", code="api_disabled")
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if len(parts) != 2 or parts[0].decode().lower() != self.keyword.lower():
            raise AuthenticationFailed("Invalid Authorization header", code="invalid_api_key")
        try:
            raw_key = parts[1].decode()
        except UnicodeError as exc:
            raise AuthenticationFailed("Invalid API key", code="invalid_api_key") from exc
        key = authenticate_raw_key(raw_key)
        if key is None:
            raise AuthenticationFailed("Invalid API key", code="invalid_api_key")
        if not ip_is_allowed(key, _client_ip(request)):
            raise AuthenticationFailed("IP address is not allowed", code="ip_not_allowed")
        return key.organization.billing_user, key

    def authenticate_header(self, _request):
        return 'Bearer realm="api"'
