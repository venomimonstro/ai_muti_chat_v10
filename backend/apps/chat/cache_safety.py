from django.conf import settings
from django.core.checks import Error, register


@register()
def chat_shared_cache_check(app_configs, **kwargs):
    backend = str(settings.CACHES.get("default", {}).get("BACKEND", ""))
    if settings.DEBUG or "locmem" not in backend.casefold():
        return []
    return [
        Error(
            "Production chat requires a shared cache backend.",
            hint=(
                "Set CACHE_URL to Redis. Multi-worker single-flight and cooperative "
                "chat cancellation are not safe with LocMemCache."
            ),
            id="chat.E001",
        )
    ]
