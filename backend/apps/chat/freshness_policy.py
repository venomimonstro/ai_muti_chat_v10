from __future__ import annotations


# Facts in these categories can become wrong without the wording "today/latest".
# Customer answers should therefore be grounded in current sources even when the
# prompt simply asks e.g. "кто президент ...", "CEO ..." or "курс доллара".
VOLATILE_FACT_MARKERS = (
    # Office holders / leadership.
    "президент",
    "премьер-министр",
    "премьер министр",
    "министр",
    "губернатор",
    "мэр ",
    "глава государства",
    "глава правительства",
    "генеральный директор",
    "исполнительный директор",
    "ceo",
    "cto",
    "cfo",
    "глава компании",
    # Financial / economic facts.
    "курс доллара",
    "курс евро",
    "курс юаня",
    "курс валют",
    "ключевая ставка",
    "ставка цб",
    "ставка центробанка",
    "инфляц",
    "котировк",
    "цена акции",
    "цена акций",
    "биткоин",
    "ethereum",
    "эфириум",
    # Software/product state that changes independently of model training.
    "последняя версия",
    "актуальная версия",
    "текущая версия",
    "версия python",
    "версия django",
    "версия react",
    "версия next.js",
    "версия nextjs",
    "версия node",
    "версия npm",
    "версия wordpress",
    "новый релиз",
    "release notes",
    # Rules, prices and commercial terms.
    "тариф",
    "цена ",
    "стоимость ",
    "закон",
    "налог",
    "штраф",
    "лимит ",
    "правила ",
)


def is_volatile_fact_query(query: str) -> bool:
    text = " ".join(str(query or "").casefold().split())
    if not text:
        return False
    return any(marker in text for marker in VOLATILE_FACT_MARKERS)


def install(live_tools_module, web_context_module) -> None:
    raw = live_tools_module.needs_web_search
    if getattr(raw, "_ai_workspace_volatile_facts", False):
        return

    def needs_web_search(query: str) -> bool:
        return bool(raw(query) or is_volatile_fact_query(query))

    needs_web_search._ai_workspace_volatile_facts = True
    needs_web_search._raw_needs_web_search = raw
    live_tools_module.needs_web_search = needs_web_search
    # web_context imported the function by value before AppConfig.ready().
    web_context_module.needs_web_search = needs_web_search
