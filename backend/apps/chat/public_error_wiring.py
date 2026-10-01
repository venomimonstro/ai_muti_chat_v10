"""Bind ASGI transport to the canonical customer-safe error classifier."""

from .public_errors import is_provider_error


def install(asgi_stream_module) -> None:
    asgi_stream_module._is_provider_error = is_provider_error
