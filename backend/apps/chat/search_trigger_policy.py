from __future__ import annotations

import re
from django.utils import timezone

EXPLICIT_SEARCH = (
    "найди в интернете", "поищи в интернете", "проверь в интернете", "посмотри в интернете",
    "поиск в интернете", "найди в сети", "поищи в сети", "поиск яндекс", "в яндексе",
    "яндекс поиск", "источники", "со ссылками", "ссылки на сайты",
)
STRONG_FRESHNESS = (
    "сегодня", "сегодняш", "последние новости", "свежие новости", "актуальные данные",
    "актуальная информация", "на данный момент", "на сегодня", "текущая цена",
    "свежая информация", "последняя версия", "текущая версия", "актуальная версия",
)
DYNAMIC_SUBJECTS = (
    "новост", "цена", "стоимость", "курс", "акци", "крипт", "биткоин", "рынок",
    "тариф", "закон", "налог", "штраф", "правил", "ваканси", "зарплат", "рейтинг",
    "отзыв", "наличи", "расписан", "рейс", "билет", "президент", "министр", "губернатор",
    "мэр ", "ceo", "директор", "версия ", "релиз", "release",
)
DECISION_MARKERS = (
    "что выбрать", "что лучше", "какой лучше", "посоветуй", "рекомендуй", "сравни",
    "лучшие", "топ ", "рейтинг", "где купить", "куда сходить", "куда поехать",
    "что открыть", "что запустить", "куда влож", "окупаем",
)
LOCAL_MARKERS = (
    "в москве", "в санкт-петербурге", "в петербурге", "в спб", "в россии", "рядом со мной",
    "поблизости", "в казани", "в екатеринбурге", "в новосибирске", "в сочи", "в перми",
    "в уфе", "в тюмени", "в челябинске", "в красноярске",
)
NON_SEARCH_TASKS = (
    "напиши", "перепиши", "исправь текст", "переведи", "сократи", "придумай", "сгенерируй текст",
    "объясни", "реши задачу", "посчитай", "формула", "напиши код", "исправь код", "рефактор",
    "продолжи разработку", "создай письмо", "составь письмо", "сделай пост",
)
YEAR_RE = re.compile(r"\b20\d{2}\b")


def search_required(query: str) -> bool:
    text = " ".join(str(query or "").casefold().split())
    if not text:
        return False
    if any(marker in text for marker in EXPLICIT_SEARCH):
        return True
    if any(marker in text for marker in STRONG_FRESHNESS):
        return True
    current_year = timezone.localdate().year
    if any(int(year) >= current_year - 1 for year in YEAR_RE.findall(text)):
        return True
    dynamic = any(marker in text for marker in DYNAMIC_SUBJECTS)
    if "сейчас" in text and dynamic:
        return True
    if dynamic and any(marker in text for marker in DECISION_MARKERS):
        return True
    if dynamic and any(marker in text for marker in LOCAL_MARKERS):
        return True
    # A pure creation/transformation request must not become a paid search merely
    # because the user says "сейчас" as an imperative/adverb.
    if any(marker in text for marker in NON_SEARCH_TASKS) and not dynamic:
        return False
    return False


def install(live_tools_module, web_context_module) -> None:
    current = live_tools_module.needs_web_search
    if getattr(current, "_ai_workspace_cost_aware_search", False):
        return

    def needs_web_search(query: str) -> bool:
        return search_required(query)

    needs_web_search._ai_workspace_cost_aware_search = True
    needs_web_search._raw_needs_web_search = current
    live_tools_module.needs_web_search = needs_web_search
    web_context_module.needs_web_search = needs_web_search
