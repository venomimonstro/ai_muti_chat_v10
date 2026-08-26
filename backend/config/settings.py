import os
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-key")
DEBUG = os.getenv("DJANGO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [
    x.strip()
    for x in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if x.strip()
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
    "apps.image_studio",
    "apps.b2b_api",
    "apps.admin_ops",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.SecurityHeadersMiddleware",
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
            "CONN_MAX_AGE": int(os.getenv("DATABASE_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
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
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "false").lower() == "true"
SESSION_COOKIE_SECURE = os.getenv(
    "DJANGO_SESSION_COOKIE_SECURE", "false" if DEBUG else "true"
).lower() == "true"
CSRF_COOKIE_SECURE = os.getenv(
    "DJANGO_CSRF_COOKIE_SECURE", "false" if DEBUG else "true"
).lower() == "true"
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "false"
).lower() == "true"
SECURE_HSTS_PRELOAD = os.getenv("DJANGO_SECURE_HSTS_PRELOAD", "false").lower() == "true"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
FILE_UPLOAD_PERMISSIONS = 0o600
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(25 * 1024 * 1024)))
if os.getenv("DJANGO_TRUST_PROXY_SSL_HEADER", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CORS_ALLOWED_ORIGINS = [
    x.strip()
    for x in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if x.strip()
]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS
CACHE_URL = os.getenv("CACHE_URL", "")
CACHES = {
    "default": (
        {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
            "OPTIONS": {"socket_connect_timeout": 2, "socket_timeout": 2},
        }
        if CACHE_URL
        else {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    )
}
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["apps.accounts.permissions.IsActiveUser"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.getenv("API_ANON_RATE", "60/min"),
        "user": os.getenv("API_USER_RATE", "300/min"),
        "login": os.getenv("API_LOGIN_RATE", "10/min"),
        "register": os.getenv("API_REGISTER_RATE", "5/hour"),
        "webhook": os.getenv("API_WEBHOOK_RATE", "30/min"),
    },
}
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "300"))
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "330"))
CELERY_BEAT_SCHEDULE = {
    "daily-financial-reconciliation": {
        "task": "apps.billing.tasks.daily_financial_reconciliation",
        "schedule": 86400.0,
    },
    "recover-stale-operations": {
        "task": "apps.admin_ops.tasks.recover_stale_operations_task",
        "schedule": 300.0,
    },
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
HISTORY_EMBEDDING_MODEL = os.getenv("HISTORY_EMBEDDING_MODEL", "local-history-hash-v1")
SEARCH_VECTOR_WEIGHT = float(os.getenv("SEARCH_VECTOR_WEIGHT", "0.50"))
SEARCH_LEXICAL_WEIGHT = float(os.getenv("SEARCH_LEXICAL_WEIGHT", "0.40"))
SEARCH_RECENCY_WEIGHT = float(os.getenv("SEARCH_RECENCY_WEIGHT", "0.10"))
SEARCH_RETRIEVAL_SCAN_LIMIT = int(os.getenv("SEARCH_RETRIEVAL_SCAN_LIMIT", "200"))
SEARCH_MIN_SEMANTIC_SCORE = float(os.getenv("SEARCH_MIN_SEMANTIC_SCORE", "0.10"))
COMPARE_ENABLED = os.getenv("COMPARE_ENABLED", "true").lower() == "true"
COMPARE_MAX_MODELS = int(os.getenv("COMPARE_MAX_MODELS", "4"))
COMPARE_MAX_OUTPUT_TOKENS = int(os.getenv("COMPARE_MAX_OUTPUT_TOKENS", "1024"))
COMPARE_CONFIRM_THRESHOLD_RUB = os.getenv("COMPARE_CONFIRM_THRESHOLD_RUB", "20.00")
IMAGES_ENABLED = os.getenv("IMAGES_ENABLED", "true").lower() == "true"
IMAGE_MAX_PROMPT_CHARS = int(os.getenv("IMAGE_MAX_PROMPT_CHARS", "4000"))
IMAGE_MAX_RESULT_BYTES = int(os.getenv("IMAGE_MAX_RESULT_BYTES", str(20 * 1024 * 1024)))
IMAGE_CONFIRM_THRESHOLD_RUB = os.getenv("IMAGE_CONFIRM_THRESHOLD_RUB", "20.00")
B2B_API_ENABLED = os.getenv("B2B_API_ENABLED", "true").lower() == "true"
B2B_API_KEY_PEPPER = os.getenv("B2B_API_KEY_PEPPER", SECRET_KEY)
B2B_API_MAX_OUTPUT_TOKENS = int(os.getenv("B2B_API_MAX_OUTPUT_TOKENS", "4096"))
B2B_API_MAX_MESSAGE_CHARS = int(os.getenv("B2B_API_MAX_MESSAGE_CHARS", "100000"))
B2B_API_RUNNING_TIMEOUT_SECONDS = int(
    os.getenv("B2B_API_RUNNING_TIMEOUT_SECONDS", "600")
)
B2B_TRUST_PROXY_IP_HEADER = os.getenv("B2B_TRUST_PROXY_IP_HEADER", "false").lower() == "true"
B2B_TRUSTED_PROXY_IPS = [
    x.strip()
    for x in os.getenv(
        "B2B_TRUSTED_PROXY_IPS",
        "127.0.0.1,10.0.0.0/8,172.16.0.0/12",
    ).split(",")
    if x.strip()
]
OPERATION_STALE_TIMEOUT_SECONDS = int(os.getenv("OPERATION_STALE_TIMEOUT_SECONDS", "900"))
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
ADMIN_MFA_ENFORCED = os.getenv("ADMIN_MFA_ENFORCED", "false").lower() == "true"

if not DEBUG:
    _weak_secret_markers = ("unsafe", "change-me", "django-insecure")
    if len(SECRET_KEY) < 50 or any(marker in SECRET_KEY for marker in _weak_secret_markers):
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be a long random value in production")
    if B2B_API_KEY_PEPPER == SECRET_KEY:
        raise ImproperlyConfigured("B2B_API_KEY_PEPPER must differ from DJANGO_SECRET_KEY")
    if PAYMENTS_LIVE_ENABLED and not YOOKASSA_API_BASE_URL.startswith("https://api.yookassa.ru"):
        raise ImproperlyConfigured("YOOKASSA_API_BASE_URL must point to the official YooKassa API")
