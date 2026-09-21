import os

from .settings import *  # noqa: F403,F401

# Test-user mode must run after Django AuthenticationMiddleware so it can
# validate the real platform-admin session before substituting request.user.
_test_user_middleware = "apps.accounts.test_user_middleware.PlatformAdminTestUserMiddleware"
if _test_user_middleware not in MIDDLEWARE:  # noqa: F405
    _auth_index = MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware")  # noqa: F405
    MIDDLEWARE.insert(_auth_index + 1, _test_user_middleware)  # noqa: F405

FRONTEND_PUBLIC_URL = os.getenv("FRONTEND_PUBLIC_URL", "http://localhost:3000").rstrip("/")
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG  # noqa: F405
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "false").lower() == "true"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@example.test")
SERVER_EMAIL = os.getenv("SERVER_EMAIL", DEFAULT_FROM_EMAIL)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))

# Owner-side procurement ledger enforcement is opt-in. Keep it separate from
# live customer payments: turning on YooKassa must not implicitly block AI calls.
PROCUREMENT_RUNTIME_FAIL_CLOSED = os.getenv(
    "PROCUREMENT_RUNTIME_FAIL_CLOSED",
    "false",
).strip().lower() in {"1", "true", "yes", "on"}

REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["client_error"] = os.getenv(  # noqa: F405
    "API_CLIENT_ERROR_RATE",
    "10/min",
)
