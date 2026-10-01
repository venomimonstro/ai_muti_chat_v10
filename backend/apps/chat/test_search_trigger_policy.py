from apps.chat.search_trigger_policy import search_required


def test_imperative_now_does_not_trigger_search():
    assert search_required("Сейчас напиши мне короткое деловое письмо") is False


def test_current_price_requires_search():
    assert search_required("Какая сейчас цена биткоина?") is True


def test_volatile_office_holder_requires_search_without_today_word():
    assert search_required("Кто президент Франции?") is True


def test_local_recommendation_requires_search():
    assert search_required("Подскажи лучшие стоматологии в Москве") is True


def test_current_year_requires_search():
    assert search_required("Лучшие CRM для малого бизнеса в 2026 году") is True


def test_timeless_explanation_does_not_trigger_search():
    assert search_required("Объясни простыми словами что такое бинарный поиск") is False
