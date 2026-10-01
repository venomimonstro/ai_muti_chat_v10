from apps.chat.search_policy_hardening import _yandex_first


def test_local_ru_recommendation_uses_yandex_first():
    assert _yandex_first("Подскажи лучшие стоматологии в Москве в 2026 году") is True


def test_explicit_yandex_request_uses_yandex_first():
    assert _yandex_first("Найди это в Яндексе") is True


def test_generic_current_fact_keeps_free_search_first():
    assert _yandex_first("Какая сейчас версия Python?") is False


def test_generic_news_keeps_free_search_first():
    assert _yandex_first("Последние новости искусственного интеллекта") is False
