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


TEAM_PRESETS = {
    "marketing": [
        {"name": "Marketing Director", "role": "Marketing Director", "kind": "generic", "level": "maximum", "tools": {"web": True, "files": True, "delegate": True}},
        {"name": "Research Analyst", "role": "Исследователь рынка", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}},
        {"name": "Content Strategist", "role": "Контент-стратег", "kind": "smm", "level": "balanced", "tools": {"web": True, "files": True}},
        {"name": "Copywriter", "role": "Копирайтер", "kind": "seo", "level": "balanced", "tools": {"web": True, "files": True}},
        {"name": "Quality Reviewer", "role": "Редактор и факт-чекер", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}},
    ],
    "content": [
        {"name": "Content Director", "role": "Главный редактор", "kind": "generic", "level": "maximum", "tools": {"web": True, "files": True, "delegate": True}},
        {"name": "Researcher", "role": "Исследователь", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}},
        {"name": "Copywriter", "role": "Копирайтер", "kind": "seo", "level": "balanced", "tools": {"web": True, "files": True}},
        {"name": "Editor", "role": "Редактор", "kind": "generic", "level": "balanced", "tools": {"files": True}},
    ],
    "sales": [
        {"name": "Sales Director", "role": "Руководитель продаж", "kind": "generic", "level": "maximum", "tools": {"web": True, "files": True, "delegate": True}},
        {"name": "Lead Researcher", "role": "Исследователь клиентов", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}},
        {"name": "Sales Copywriter", "role": "Автор коммуникаций", "kind": "generic", "level": "balanced", "tools": {"files": True}},
        {"name": "Sales Analyst", "role": "Аналитик", "kind": "generic", "level": "balanced", "tools": {"files": True}},
    ],
    "generic": [
        {"name": "Team Director", "role": "Руководитель", "kind": "generic", "level": "maximum", "tools": {"web": True, "files": True, "delegate": True}},
        {"name": "Researcher", "role": "Исследователь", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}},
        {"name": "Specialist", "role": "Исполнитель", "kind": "generic", "level": "balanced", "tools": {"web": True, "files": True}},
        {"name": "Reviewer", "role": "Контроль качества", "kind": "generic", "level": "balanced", "tools": {"files": True}},
    ],
}


def team_draft(description: str) -> dict:
    kind = infer_team_kind(description)
    if kind == "development":
        return {"kind": kind, "name": "Dev Team", "members": []}
    members = deepcopy(TEAM_PRESETS[kind])
    for item in members:
        item["graph"] = graph_for_kind(item["kind"])
    names = {
        "marketing": "Маркетинговый отдел",
        "content": "Контент-команда",
        "sales": "Отдел продаж",
        "generic": "AI-команда",
    }
    return {"kind": kind, "name": names[kind], "members": members}
