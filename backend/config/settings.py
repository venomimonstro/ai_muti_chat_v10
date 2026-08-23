import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-key")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [
    x for x in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if x
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "apps.accounts",
    "apps.ai_registry",
    "apps.billing",
    "apps.payments",
    "apps.chat",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

database_url = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
parsed = urlparse(database_url)
if parsed.scheme.startswith("postgres"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.lstrip("/"),
            "USER": parsed.username,
            "PASSWORD": parsed.password,
            "HOST": parsed.hostname,
            "PORT": parsed.port or 5432,
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": parsed.path}}

AUTH_PASSWORD_VALIDATORS = (
    []
    if DEBUG
    else [
        {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
        {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    ]
)
AUTH_USER_MODEL = "accounts.User"
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
CORS_ALLOWED_ORIGINS = [
    x for x in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",") if x
]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
SIGNUP_PROMO_RUB = os.getenv("SIGNUP_PROMO_RUB", "25.00")
AI_PROVIDER_TIMEOUT_SECONDS = float(os.getenv("AI_PROVIDER_TIMEOUT_SECONDS", "120"))
AI_PROVIDER_MAX_ATTEMPTS = int(os.getenv("AI_PROVIDER_MAX_ATTEMPTS", "2"))
AI_CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("AI_CIRCUIT_FAILURE_THRESHOLD", "3"))
AI_CIRCUIT_COOLDOWN_SECONDS = int(os.getenv("AI_CIRCUIT_COOLDOWN_SECONDS", "60"))
PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "false").lower() == "true"
PAYMENTS_LIVE_ENABLED = os.getenv("PAYMENTS_LIVE_ENABLED", "false").lower() == "true"
PAYMENT_RETURN_URL = os.getenv(
    "PAYMENT_RETURN_URL", "http://localhost:3000/settings/billing/return"
)
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")
YOOKASSA_API_BASE_URL = os.getenv("YOOKASSA_API_BASE_URL", "https://api.yookassa.ru/v3")
PAYMENTS_FISCALIZATION_MODE = os.getenv("PAYMENTS_FISCALIZATION_MODE", "disabled")
PAYMENTS_VAT_CODE = int(os.getenv("PAYMENTS_VAT_CODE", "1"))
PAYMENT_MIN_RUB = os.getenv("PAYMENT_MIN_RUB", "100.00")
PAYMENT_MAX_RUB = os.getenv("PAYMENT_MAX_RUB", "100000.00")
