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
    "apps.projects",
    "apps.files",
    "apps.workspace_search",
    "apps.memory_store",
    "apps.evals",
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
MEDIA_ROOT = BASE_DIR / "media"
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
CELERY_BEAT_SCHEDULE = {
    "daily-financial-reconciliation": {
        "task": "apps.billing.tasks.daily_financial_reconciliation",
        "schedule": 86400.0,
    }
}
SIGNUP_PROMO_RUB = os.getenv("SIGNUP_PROMO_RUB", "25.00")
AI_PROVIDER_TIMEOUT_SECONDS = float(os.getenv("AI_PROVIDER_TIMEOUT_SECONDS", "120"))
AI_PROVIDER_MAX_ATTEMPTS = int(os.getenv("AI_PROVIDER_MAX_ATTEMPTS", "2"))
AI_CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("AI_CIRCUIT_FAILURE_THRESHOLD", "3"))
AI_CIRCUIT_COOLDOWN_SECONDS = int(os.getenv("AI_CIRCUIT_COOLDOWN_SECONDS", "60"))
FILE_MAX_UPLOAD_BYTES = int(os.getenv("FILE_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
FILE_MAX_UNCOMPRESSED_BYTES = int(os.getenv("FILE_MAX_UNCOMPRESSED_BYTES", str(100 * 1024 * 1024)))
FILE_MAX_ARCHIVE_ENTRIES = int(os.getenv("FILE_MAX_ARCHIVE_ENTRIES", "1000"))
FILE_MAX_COMPRESSION_RATIO = int(os.getenv("FILE_MAX_COMPRESSION_RATIO", "100"))
FILE_MAX_EXTRACTED_CHARS = int(os.getenv("FILE_MAX_EXTRACTED_CHARS", "2000000"))
FILE_CHUNK_CHARS = int(os.getenv("FILE_CHUNK_CHARS", "4000"))
FILE_CHUNK_OVERLAP_CHARS = int(os.getenv("FILE_CHUNK_OVERLAP_CHARS", "200"))
RAG_EMBEDDING_DIMENSIONS = 384
RAG_EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "local-hash-v1")
RAG_VECTOR_WEIGHT = float(os.getenv("RAG_VECTOR_WEIGHT", "0.65"))
RAG_LEXICAL_WEIGHT = float(os.getenv("RAG_LEXICAL_WEIGHT", "0.35"))
AUTO_MEMORY_ENABLED = os.getenv("AUTO_MEMORY_ENABLED", "false").lower() == "true"
AUTO_MEMORY_MAX_CANDIDATES = int(os.getenv("AUTO_MEMORY_MAX_CANDIDATES", "3"))
SMART_CONTEXT_RECENT_TURNS = int(os.getenv("SMART_CONTEXT_RECENT_TURNS", "6"))
SMART_CONTEXT_RECENT_SHARE = float(os.getenv("SMART_CONTEXT_RECENT_SHARE", "0.55"))
SMART_CONTEXT_PROJECT_TOKENS = int(os.getenv("SMART_CONTEXT_PROJECT_TOKENS", "2000"))
SMART_CONTEXT_MEMORY_TOKENS = int(os.getenv("SMART_CONTEXT_MEMORY_TOKENS", "1600"))
SMART_CONTEXT_OLD_MESSAGE_TOKENS = int(os.getenv("SMART_CONTEXT_OLD_MESSAGE_TOKENS", "1200"))
SMART_CONTEXT_FILE_TOKENS = int(os.getenv("SMART_CONTEXT_FILE_TOKENS", "2400"))
SMART_CONTEXT_SUMMARY_TOKENS = int(os.getenv("SMART_CONTEXT_SUMMARY_TOKENS", "1200"))
SMART_CONTEXT_MEMORY_LIMIT = int(os.getenv("SMART_CONTEXT_MEMORY_LIMIT", "8"))
SMART_CONTEXT_OLD_MESSAGE_LIMIT = int(os.getenv("SMART_CONTEXT_OLD_MESSAGE_LIMIT", "4"))
SMART_CONTEXT_FILE_CHUNK_LIMIT = int(os.getenv("SMART_CONTEXT_FILE_CHUNK_LIMIT", "4"))
SMART_CONTEXT_RETRIEVAL_SCAN_LIMIT = int(os.getenv("SMART_CONTEXT_RETRIEVAL_SCAN_LIMIT", "200"))
SMART_CONTEXT_MIN_RELEVANCE = float(os.getenv("SMART_CONTEXT_MIN_RELEVANCE", "0.08"))
SMART_CONTEXT_SUMMARY_CHARS = int(os.getenv("SMART_CONTEXT_SUMMARY_CHARS", "6000"))
EVAL_MIN_AVERAGE_SCORE = float(os.getenv("EVAL_MIN_AVERAGE_SCORE", "0.70"))
EVAL_MAX_HALLUCINATION_RATE = float(os.getenv("EVAL_MAX_HALLUCINATION_RATE", "0.05"))
EVAL_MAX_ERROR_RATE = float(os.getenv("EVAL_MAX_ERROR_RATE", "0.02"))
EVAL_MAX_REGRESSION = float(os.getenv("EVAL_MAX_REGRESSION", "0.05"))
EVAL_MAX_OUTPUT_TOKENS = int(os.getenv("EVAL_MAX_OUTPUT_TOKENS", "1024"))
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
