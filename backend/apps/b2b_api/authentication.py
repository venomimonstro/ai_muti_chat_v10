from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .keys import authenticate_raw_key, ip_is_allowed


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
        if not ip_is_allowed(key, request.META.get("REMOTE_ADDR", "")):
            raise AuthenticationFailed("IP address is not allowed", code="ip_not_allowed")
        return key.organization.billing_user, key

    def authenticate_header(self, _request):
        return 'Bearer realm="api"'
