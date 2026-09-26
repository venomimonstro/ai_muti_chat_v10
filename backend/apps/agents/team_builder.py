from copy import deepcopy

from .planner import graph_for_kind


def infer_team_kind(description: str) -> str:
    text = str(description or "").casefold()
    if any(word in text for word in ("разработ", "код", "github", "программ", "backend", "frontend")):
        return "development"
    if any(word in text for word in ("продаж", "лид", "клиент", "crm", "коммерческ")):
        return "sales"
    if any(word in text for word in ("стать", "редактор", "копирайт", "блог", "медиа", "контент", "seo")):
        return "content"
    if any(word in text for word in ("маркет", "smm", "соцсет", "реклам", "таргет", "бренд")):
        return "marketing"
    return "generic"


# Generic team runtime performs one paid LLM call per enabled member. A team
# created for a simple task must therefore not silently become a five-model
# workflow. "core" roles are enough for ordinary execution; optional roles are
# added only when the user's description actually calls for broader analysis.
TEAM_PRESETS = {
    "marketing": [
        {"name": "Marketing Director", "role": "Marketing Director", "kind": "generic", "level": "maximum", "tools": {"web": True, "files": True, "delegate": True}, "core": True},
        {"name": "Research Analyst", "role": "Исследователь рынка", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}, "core": False},
        {"name": "Content Strategist", "role": "Контент-стратег", "kind": "smm", "level": "balanced", "tools": {"web": True, "files": True}, "core": False},
        {"name": "Copywriter", "role": "Копирайтер", "kind": "seo", "level": "balanced", "tools": {"web": True, "files": True}, "core": True},
        {"name": "Quality Reviewer", "role": "Редактор и факт-чекер", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}, "core": True},
    ],
    "content": [
        {"name": "Content Director", "role": "Главный редактор", "kind": "generic", "level": "maximum", "tools": {"web": True, "files": True, "delegate": True}, "core": True},
        {"name": "Researcher", "role": "Исследователь", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}, "core": False},
        {"name": "Copywriter", "role": "Копирайтер", "kind": "seo", "level": "balanced", "tools": {"web": True, "files": True}, "core": True},
        {"name": "Editor", "role": "Редактор", "kind": "generic", "level": "balanced", "tools": {"files": True}, "core": True},
    ],
    "sales": [
        {"name": "Sales Director", "role": "Руководитель продаж", "kind": "generic", "level": "maximum", "tools": {"web": True, "files": True, "delegate": True}, "core": True},
        {"name": "Lead Researcher", "role": "Исследователь клиентов", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}, "core": False},
        {"name": "Sales Copywriter", "role": "Автор коммуникаций", "kind": "generic", "level": "balanced", "tools": {"files": True}, "core": True},
        {"name": "Sales Analyst", "role": "Аналитик", "kind": "generic", "level": "balanced", "tools": {"files": True}, "core": True},
    ],
    "generic": [
        {"name": "Team Director", "role": "Руководитель", "kind": "generic", "level": "maximum", "tools": {"web": True, "files": True, "delegate": True}, "core": True},
        {"name": "Researcher", "role": "Исследователь", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}, "core": False},
        {"name": "Specialist", "role": "Исполнитель", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}, "core": True},
        {"name": "Reviewer", "role": "Контроль качества", "kind": "generic", "level": "balanced", "tools": {"files": True}, "core": True},
    ],
}


COMPLEXITY_MARKERS = (
    "стратег",
    "исслед",
    "конкурент",
    "рынок",
    "аналит",
    "комплекс",
    "полный цикл",
    "несколько канал",
    "контент-план",
    "контент план",
    "воронк",
    "сегмент",
    "аудит",
)


def needs_extended_team(description: str) -> bool:
    text = str(description or "").casefold()
    score = sum(1 for marker in COMPLEXITY_MARKERS if marker in text)
    # Explicit multi-stage wording is another signal, but long boilerplate from
    # the UI by itself must not inflate team size.
    multi_action = sum(text.count(token) for token in (",", ";", " и ")) >= 4
    return score >= 2 or (score >= 1 and multi_action)


def team_draft(description: str) -> dict:
    kind = infer_team_kind(description)
    if kind == "development":
        return {"kind": kind, "name": "Dev Team", "members": [], "size_mode": "dev"}

    extended = needs_extended_team(description)
    raw_members = deepcopy(TEAM_PRESETS[kind])
    members = raw_members if extended else [item for item in raw_members if item.get("core")]
    for item in members:
        item.pop("core", None)
        item["graph"] = graph_for_kind(item["kind"])

    names = {
        "marketing": "Маркетинговый отдел",
        "content": "Контент-команда",
        "sales": "Отдел продаж",
        "generic": "AI-команда",
    }
    return {
        "kind": kind,
        "name": names[kind],
        "members": members,
        "size_mode": "extended" if extended else "compact",
    }
