from copy import deepcopy


GRAPHS = {
    "smm": {
        "nodes": [
            {
                "id": "research",
                "title": "Найти актуальные темы",
                "type": "web",
                "prompt": "Найди свежие темы, новости, вопросы аудитории и инфоповоды, относящиеся к задаче и бизнесу. Используй только актуальные найденные данные; не выдавай память модели за свежую информацию.",
            },
            {
                "id": "plan",
                "title": "Выбрать тему и формат",
                "type": "llm",
                "prompt": "На основе исследования выбери одну наиболее полезную тему для целевой аудитории. Определи цель публикации, основной тезис, формат и призыв к действию. Не повторяй уже найденные факты без необходимости.",
            },
            {
                "id": "copy",
                "title": "Подготовить текст",
                "type": "llm",
                "prompt": "Напиши готовый текст публикации человеческим языком: сильное начало, полезная основная часть и уместный призыв к действию. Не добавляй неподтверждённые цифры, обещания и факты.",
            },
            {
                "id": "fact_check",
                "title": "Проверить текст и факты",
                "type": "review",
                "prompt": "Проверь подготовленный текст: факты, логические противоречия, повторы, канцелярит, чрезмерные обещания и несоответствие исходной задаче. Верни исправленную финальную версию текста, а не только список замечаний.",
            },
            {
                "id": "visual",
                "title": "Подготовить изображение",
                "type": "image",
            },
            {
                "id": "approval",
                "title": "Подтверждение публикации",
                "type": "approval",
            },
            {
                "id": "publish",
                "title": "Сохранить материал на сайте",
                "type": "publish",
                "status": "draft",
            },
            {"id": "finish", "title": "Завершить задачу", "type": "finish"},
        ],
    },
    "seo": {
        "nodes": [
            {
                "id": "research",
                "title": "Исследовать тему и выдачу",
                "type": "web",
                "prompt": "Изучи актуальную поисковую выдачу и доступные источники по теме. Выдели намерения пользователей, обязательные подтемы, вопросы и факты. Не копируй тексты конкурентов и не придумывай частотность запросов без источника.",
            },
            {
                "id": "files",
                "title": "Изучить материалы проекта",
                "type": "files",
                "prompt": "Найди в материалах проекта факты о продукте, услуге, условиях, преимуществах и ограничениях, которые относятся к будущему материалу. Используй только реально найденную информацию.",
            },
            {
                "id": "outline",
                "title": "Собрать структуру",
                "type": "llm",
                "prompt": "Составь логичную SEO-структуру материала под намерение пользователя. Один H1, далее содержательные H2/H3; исключи пустые разделы и искусственное повторение ключевых слов.",
            },
            {
                "id": "draft",
                "title": "Написать материал",
                "type": "llm",
                "prompt": "Напиши полный материал по утверждённой структуре. Отвечай на вопросы пользователя конкретно, используй факты из исследования и материалов проекта, не выдумывай характеристики компании и не делай keyword stuffing.",
            },
            {
                "id": "seo_review",
                "title": "SEO и факт-проверка",
                "type": "review",
                "prompt": "Проверь итоговый материал на полноту ответа, фактическую опору, структуру заголовков, естественность формулировок, дубли и переспам. Верни улучшенную финальную версию статьи целиком.",
            },
            {
                "id": "approval",
                "title": "Подтверждение публикации",
                "type": "approval",
            },
            {
                "id": "publish",
                "title": "Сохранить материал на сайте",
                "type": "publish",
                "status": "draft",
            },
            {"id": "finish", "title": "Завершить задачу", "type": "finish"},
        ],
    },
    "development": {
        "nodes": [
            {"id": "inspect", "title": "Изучить репозиторий", "type": "github_read"},
            {
                "id": "plan",
                "title": "Подготовить технический план",
                "type": "llm",
                "prompt": "Сформируй минимальный безопасный план изменений на основании реально прочитанного репозитория. Укажи затрагиваемые компоненты, риски и проверки.",
            },
            {"id": "approval", "title": "Подтвердить опасные изменения", "type": "approval"},
            {"id": "branch", "title": "Создать рабочую ветку", "type": "github_write"},
            {"id": "implement", "title": "Внести изменения", "type": "code"},
            {"id": "tests", "title": "Запустить тесты", "type": "sandbox"},
            {
                "id": "review",
                "title": "Code review и Security",
                "type": "review",
                "prompt": "Проверь предложенные изменения на регрессии, безопасность, соответствие задаче и достаточность тестов. Не считай действие выполненным, если инструмент его реально не выполнял.",
            },
            {"id": "commit_approval", "title": "Подтвердить публикацию изменений", "type": "approval"},
            {"id": "commit", "title": "Создать commit / PR", "type": "github_write"},
        ],
    },
    "qa": {
        "nodes": [
            {
                "id": "requirements",
                "title": "Разобрать требования",
                "type": "llm",
                "prompt": "Разложи задачу на проверяемые требования, негативные сценарии и критерии приёмки. Не придумывай требования, которых нет во входных данных.",
            },
            {"id": "inspect", "title": "Изучить проект", "type": "github_read"},
            {
                "id": "cases",
                "title": "Составить тестовые сценарии",
                "type": "llm",
                "prompt": "Составь компактный набор позитивных, негативных, граничных и регрессионных сценариев по реально доступному коду и требованиям.",
            },
            {"id": "tests", "title": "Запустить проверки", "type": "sandbox"},
            {
                "id": "report",
                "title": "Оформить дефекты и риски",
                "type": "review",
                "prompt": "Сформируй отчёт только по подтверждённым результатам проверок: что прошло, что не прошло, как воспроизвести дефекты, уровень риска и что проверить повторно.",
            },
        ],
    },
    "generic": {
        "nodes": [
            {
                "id": "understand",
                "title": "Разобрать задачу",
                "type": "llm",
                "prompt": "Определи конкретный ожидаемый результат, ограничения и недостающие данные. Не расширяй задачу без необходимости.",
            },
            {
                "id": "research",
                "title": "Собрать нужные данные",
                "type": "research",
                "prompt": "Собери только данные, необходимые для выполнения текущей задачи. Отделяй найденные актуальные факты от выводов модели.",
            },
            {
                "id": "execute",
                "title": "Подготовить результат",
                "type": "llm",
                "prompt": "Подготовь законченный практический результат по задаче, используя собранные данные и контекст пользователя.",
            },
            {
                "id": "review",
                "title": "Проверить качество",
                "type": "review",
                "prompt": "Проверь результат на ошибки, противоречия, пропущенные требования и неподтверждённые утверждения. Верни исправленный финальный вариант.",
            },
            {"id": "finish", "title": "Завершить задачу", "type": "finish"},
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
