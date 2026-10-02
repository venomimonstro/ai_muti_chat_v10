from __future__ import annotations

from apps.ai_registry.models import ProviderApiKey


EXPLICIT_YANDEX_MARKERS = (
    "яндекс",
    "yandex",
    "поиск яндекс",
    "яндекс поиск",
    "в яндексе",
)
LOCAL_RU_MARKERS = (
    "в москве",
    "в санкт-петербурге",
    "в петербурге",
    "в спб",
    "в россии",
    "в казани",
    "в екатеринбурге",
    "в новосибирске",
    "в сочи",
    "в перми",
    "в уфе",
    "в тюмени",
    "в челябинске",
    "в красноярске",
    "рядом со мной",
    "поблизости",
)
LOCAL_DECISION_MARKERS = (
    "лучшие",
    "топ ",
    "рейтинг",
    "отзыв",
    "где купить",
    "где найти",
    "куда сходить",
    "посоветуй",
    "рекомендуй",
    "стоматолог",
    "клиник",
    "врач",
    "ресторан",
    "отел",
    "гостиниц",
    "магазин",
    "сервис",
    "аптек",
    "ваканси",
)


def _yandex_first(query: str) -> bool:
    text = " ".join(str(query or "").casefold().split())
    # Paid Yandex search is opt-in per request. Local/commercial intent still
    # requires current search, but it starts with the free redundant SearXNG path.
    # This avoids turning an ordinary recommendation into an unexpected paid call
    # and improves availability when the paid credential is degraded.
    return any(marker in text for marker in EXPLICIT_YANDEX_MARKERS)


def install(paid_search_module) -> None:
    """Harden paid-search policy without touching the transport/billing pipeline.

    Rules:
    - Yandex-first only for an explicit Yandex request. All other current/local
      queries try the redundant free SearXNG path first.
    - DEGRADED/DISABLED credentials never receive customer traffic.
    - Generic query-level search failures do not degrade the credential. Only
      confirmed auth/credit failures do; temporary/no-result errors stay local to
      the request and may fall back to SearXNG.
    """
    if getattr(paid_search_module, "_ai_workspace_search_policy_hardened", False):
        return

    raw_secret = paid_search_module._account_secret
    raw_mark_key = paid_search_module._mark_key

    def account_secret(account) -> str:
        if account.api_key_id:
            key = account.api_key
            if not key.enabled or key.health_state not in {
                ProviderApiKey.HealthState.HEALTHY,
                ProviderApiKey.HealthState.UNKNOWN,
            }:
                return ""
        return raw_secret(account)

    def mark_key(account, *, healthy: bool, error_code: str = ""):
        code = str(error_code or "").strip().casefold()
        if healthy:
            return raw_mark_key(account, healthy=True, error_code="")
        if code not in {"authentication_error", "credit_balance_exhausted"}:
            return None
        return raw_mark_key(account, healthy=False, error_code=code)

    paid_search_module._premium_search = _yandex_first
    paid_search_module._account_secret = account_secret
    paid_search_module._mark_key = mark_key
    paid_search_module._ai_workspace_search_policy_hardened = True
