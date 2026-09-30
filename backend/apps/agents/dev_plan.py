import json
import re
from collections import deque

from django.core.exceptions import ValidationError


MAX_PLAN_TASKS = 12
MAX_DEPENDENCIES_PER_TASK = 8
MAX_TITLE_CHARS = 180
MAX_ACCEPTANCE_CHARS = 500
ALLOWED_ROLES = {
    "Architecture",
    "Development",
    "QA & Security",
    "Final Review",
}
_PLAN_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)


def director_output_contract():
    return (
        "Ты Engineering Director. Сначала декомпозируй задачу в исполнимый DAG. "
        "В конце ответа ОБЯЗАТЕЛЬНО верни JSON-блок вида "
        '{"dev_plan":{"summary":"кратко","tasks":['
        '{"id":"architecture","title":"Проверить архитектуру","role":"Architecture","depends_on":[],"acceptance":"критерий"},'
        '{"id":"development","title":"Реализовать изменение","role":"Development","depends_on":["architecture"],"acceptance":"код и проверки"},'
        '{"id":"qa","title":"Проверить результат","role":"QA & Security","depends_on":["development"],"acceptance":"нет регрессий"}'
        ']}}. '
        "Допустимые роли: Architecture, Development, QA & Security, Final Review. "
        "Не создавай циклы, не дублируй id и не включай Approval/GitHub write как AI-задачи: защищённые действия система добавляет сама."
    )


def _candidate_payload(text):
    text = str(text or "").strip()
    candidates = [*reversed(_PLAN_FENCE_RE.findall(text))]
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])
    candidates.append(text)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("dev_plan"), dict):
            return parsed["dev_plan"]
    return None


def _safe_id(value):
    raw = str(value or "").strip().lower()
    if not raw or len(raw) > 64 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", raw):
        raise ValidationError("Director вернул некорректный id задачи")
    return raw


def _normalize_task(raw):
    if not isinstance(raw, dict):
        raise ValidationError("Director вернул некорректную задачу плана")
    task_id = _safe_id(raw.get("id"))
    role = str(raw.get("role") or "").strip()
    if role not in ALLOWED_ROLES:
        raise ValidationError(f"Director назначил неподдерживаемую роль: {role or 'empty'}")
    title = str(raw.get("title") or "").strip()[:MAX_TITLE_CHARS]
    if not title:
        raise ValidationError(f"Для задачи {task_id} отсутствует title")
    dependencies = raw.get("depends_on") or []
    if not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES_PER_TASK:
        raise ValidationError(f"Некорректные зависимости задачи {task_id}")
    normalized_dependencies = []
    for dependency in dependencies:
        dep_id = _safe_id(dependency)
        if dep_id == task_id:
            raise ValidationError(f"Задача {task_id} не может зависеть от себя")
        if dep_id not in normalized_dependencies:
            normalized_dependencies.append(dep_id)
    acceptance = str(raw.get("acceptance") or "").strip()[:MAX_ACCEPTANCE_CHARS]
    return {
        "id": task_id,
        "title": title,
        "role": role,
        "depends_on": normalized_dependencies,
        "acceptance": acceptance,
        "state": "pending",
    }


def _topological_order(tasks):
    by_id = {task["id"]: task for task in tasks}
    indegree = {task_id: 0 for task_id in by_id}
    children = {task_id: [] for task_id in by_id}
    for task in tasks:
        for dependency in task["depends_on"]:
            if dependency not in by_id:
                raise ValidationError(
                    f"Задача {task['id']} зависит от отсутствующей задачи {dependency}"
                )
            indegree[task["id"]] += 1
            children[dependency].append(task["id"])
    queue = deque(task["id"] for task in tasks if indegree[task["id"]] == 0)
    ordered = []
    while queue:
        task_id = queue.popleft()
        ordered.append(task_id)
        for child in children[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(ordered) != len(tasks):
        raise ValidationError("Director создал циклический план")
    return ordered


def parse_director_plan(text):
    payload = _candidate_payload(text)
    if payload is None:
        return None
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValidationError("Director plan должен содержать непустой tasks")
    if len(raw_tasks) > MAX_PLAN_TASKS:
        raise ValidationError(f"Director создал слишком много задач: максимум {MAX_PLAN_TASKS}")
    tasks = [_normalize_task(item) for item in raw_tasks]
    ids = [task["id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValidationError("Director создал повторяющиеся id задач")
    order = _topological_order(tasks)
    task_by_id = {task["id"]: task for task in tasks}
    ordered_tasks = [task_by_id[task_id] for task_id in order]
    return {
        "version": 2,
        "summary": str(payload.get("summary") or "").strip()[:1000],
        "tasks": ordered_tasks,
    }


def fallback_dev_plan():
    return {
        "version": 2,
        "summary": "Безопасный последовательный план Dev Studio",
        "tasks": [
            {
                "id": "architecture",
                "title": "Проверить архитектуру и риски",
                "role": "Architecture",
                "depends_on": [],
                "acceptance": "Есть технический план и выявлены затрагиваемые компоненты",
                "state": "pending",
            },
            {
                "id": "development",
                "title": "Реализовать требуемые изменения",
                "role": "Development",
                "depends_on": ["architecture"],
                "acceptance": "Изменения минимальны, конкретны и готовы к проверке",
                "state": "pending",
            },
            {
                "id": "qa",
                "title": "Проверить качество и безопасность",
                "role": "QA & Security",
                "depends_on": ["development"],
                "acceptance": "Проверены регрессии, безопасность и результат задачи",
                "state": "pending",
            },
        ],
    }


def plan_for_run(text):
    parsed = parse_director_plan(text)
    return parsed or fallback_dev_plan()


def plan_rows_for_ui(plan):
    rows = []
    for task in (plan or {}).get("tasks") or []:
        rows.append(
            {
                "id": task["id"],
                "title": task["title"],
                "role": task["role"],
                "depends_on": list(task.get("depends_on") or []),
                "acceptance": task.get("acceptance") or "",
                "state": task.get("state") or "pending",
            }
        )
    rows.extend(
        [
            {"id": "approval", "title": "Подтверждение изменений", "role": "System", "depends_on": [], "acceptance": "", "state": "conditional"},
            {"id": "sandbox-write", "title": "Sandbox + GitHub write", "role": "System", "depends_on": ["approval"], "acceptance": "", "state": "conditional"},
            {"id": "final", "title": "Финальная приёмка", "role": "Final Review", "depends_on": [], "acceptance": "", "state": "pending"},
        ]
    )
    return rows
