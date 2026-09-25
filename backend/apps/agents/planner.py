from copy import deepcopy


GRAPHS = {
    "smm": {
        "nodes": [
            {"id": "research", "title": "Найти актуальные темы", "type": "web"},
            {"id": "plan", "title": "Собрать контент-план", "type": "llm"},
            {"id": "copy", "title": "Подготовить текст", "type": "llm"},
            {"id": "fact_check", "title": "Проверить факты", "type": "review"},
            {"id": "visual", "title": "Подготовить изображение", "type": "image"},
            {"id": "approval", "title": "Подтверждение публикации", "type": "approval"},
            {"id": "publish", "title": "Сохранить материал на сайте", "type": "publish", "status": "draft"},
            {"id": "analytics", "title": "Оценить результат", "type": "analytics"},
        ],
    },
    "seo": {
        "nodes": [
            {"id": "research", "title": "Исследовать тему и выдачу", "type": "web"},
            {"id": "outline", "title": "Собрать структуру", "type": "llm"},
            {"id": "draft", "title": "Написать материал", "type": "llm"},
            {"id": "seo_review", "title": "SEO и факт-проверка", "type": "review"},
            {"id": "approval", "title": "Подтверждение публикации", "type": "approval"},
            {"id": "publish", "title": "Сохранить материал на сайте", "type": "publish", "status": "draft"},
            {"id": "monitor", "title": "Проверить результат позже", "type": "analytics"},
        ],
    },
    "development": {
        "nodes": [
            {"id": "inspect", "title": "Изучить репозиторий", "type": "github_read"},
            {"id": "plan", "title": "Подготовить технический план", "type": "llm"},
            {"id": "approval", "title": "Подтвердить опасные изменения", "type": "approval"},
            {"id": "branch", "title": "Создать рабочую ветку", "type": "github_write"},
            {"id": "implement", "title": "Внести изменения", "type": "code"},
            {"id": "tests", "title": "Запустить тесты", "type": "sandbox"},
            {"id": "review", "title": "Code review и Security", "type": "review"},
            {"id": "commit_approval", "title": "Подтвердить публикацию изменений", "type": "approval"},
            {"id": "commit", "title": "Создать commit / PR", "type": "github_write"},
        ],
    },
    "qa": {
        "nodes": [
            {"id": "requirements", "title": "Разобрать требования", "type": "llm"},
            {"id": "inspect", "title": "Изучить проект", "type": "github_read"},
            {"id": "cases", "title": "Составить тестовые сценарии", "type": "llm"},
            {"id": "tests", "title": "Запустить проверки", "type": "sandbox"},
            {"id": "report", "title": "Оформить дефекты и риски", "type": "review"},
        ],
    },
    "generic": {
        "nodes": [
            {"id": "understand", "title": "Разобрать задачу", "type": "llm"},
            {"id": "research", "title": "Собрать нужные данные", "type": "research"},
            {"id": "execute", "title": "Подготовить результат", "type": "llm"},
            {"id": "review", "title": "Проверить качество", "type": "review"},
            {"id": "approval", "title": "Подтвердить внешнее действие", "type": "approval"},
        ],
    },
}


def _finish(graph):
    graph = deepcopy(graph)
    nodes = graph.get("nodes", [])
    graph["edges"] = [
        {"from": nodes[index]["id"], "to": nodes[index + 1]["id"]}
        for index in range(max(0, len(nodes) - 1))
    ]
    graph["version"] = 1
    return graph


def graph_for_kind(kind):
    return _finish(GRAPHS.get(kind, GRAPHS["generic"]))


def infer_kind(description):
    text = str(description or "").casefold()
    if any(word in text for word in ("smm", "соцсет", "социальн", "пост", "контент-план", "контент план")):
        return "smm"
    if any(word in text for word in ("seo", "стать", "копирайт", "сайт", "семантик", "поисков")):
        return "seo"
    if any(word in text for word in ("код", "разработ", "github", "репозитор", "backend", "frontend", "программ")):
        return "development"
    if any(word in text for word in ("qa", "тест", "регресс", "баг", "ошибк")):
        return "qa"
    return "generic"


def draft_from_description(description):
    kind = infer_kind(description)
    presets = {
        "smm": (
            "SMM-специалист",
            "SMM specialist",
            {"web": True, "files": True, "images": True, "publish": "approval"},
        ),
        "seo": (
            "SEO-специалист",
            "SEO specialist",
            {"web": True, "files": True, "publish": "approval"},
        ),
        "development": (
            "Разработчик",
            "Software Engineer",
            {"github": True, "files": True, "shell": "sandbox", "write_code": True, "merge": "approval"},
        ),
        "qa": (
            "QA-инженер",
            "QA Engineer",
            {"github": True, "files": True, "shell": "sandbox", "write_code": False},
        ),
        "generic": (
            "AI-сотрудник",
            "AI specialist",
            {"web": True, "files": True},
        ),
    }
    name, role, tools = presets[kind]
    return {
        "kind": kind,
        "name": name,
        "role": role,
        "objective": str(description or "").strip(),
        "tool_policy": tools,
        "graph": graph_for_kind(kind),
        "autonomy": "controlled" if kind in {"development", "qa"} else "semi_autonomous",
        "system_level": "maximum" if kind == "development" else "balanced",
    }
