import json
import re

from django.core.exceptions import ValidationError


MAX_CHANGES = 12
MAX_CONTENT_CHARS = 250000
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)


def _candidate_json(text):
    text = str(text or "").strip()
    fenced = _JSON_FENCE_RE.findall(text)
    candidates = [*reversed(fenced), text]
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _safe_path(value):
    path = str(value or "").replace("\\", "/").strip().lstrip("/")
    if not path or path.startswith(".") or ".." in path.split("/") or "\x00" in path:
        raise ValidationError("Developer предложил небезопасный путь файла")
    return path


def parse_change_proposal(text):
    payload = _candidate_json(text)
    if not payload:
        return []
    raw_changes = payload.get("changes")
    if not isinstance(raw_changes, list):
        return []
    if len(raw_changes) > MAX_CHANGES:
        raise ValidationError(f"Developer предложил слишком много файлов: максимум {MAX_CHANGES}")
    changes = []
    total = 0
    seen_paths = set()
    for raw in raw_changes:
        if not isinstance(raw, dict):
            raise ValidationError("Некорректный элемент changes")
        path = _safe_path(raw.get("path"))
        operation = str(raw.get("operation") or "update").strip().lower()
        if operation not in {"create", "update", "delete"}:
            raise ValidationError("Dev Studio разрешает только create/update/delete")
        if path in seen_paths:
            raise ValidationError(f"Developer предложил несколько операций для одного файла: {path}")
        seen_paths.add(path)

        item = {
            "path": path,
            "operation": operation,
            "reason": str(raw.get("reason") or "").strip()[:500],
        }
        if operation in {"create", "update"}:
            content = raw.get("content")
            if not isinstance(content, str):
                raise ValidationError("Для create/update требуется полный итоговый текст content")
            total += len(content)
            if total > MAX_CONTENT_CHARS:
                raise ValidationError("Суммарный объём предложенных изменений слишком большой")
            item["content"] = content
        elif "content" in raw and raw.get("content") not in {None, ""}:
            raise ValidationError("Для delete не передавайте content")
        changes.append(item)
    return changes


def developer_output_contract():
    return (
        "Если нужны изменения кода, в самом конце ответа ОБЯЗАТЕЛЬНО добавь валидный JSON-блок вида: "
        "{\"changes\":[{\"path\":\"relative/path.py\",\"operation\":\"update\",\"content\":\"ПОЛНЫЙ новый текст файла\",\"reason\":\"зачем\"}]}. "
        "Разрешённые операции: create, update, delete. Для create/update обязательно передай полный итоговый content. "
        "Для delete передай path, operation=delete и reason без content; удаляй файл только когда это действительно нужно для задачи. "
        "Не предлагай несколько операций для одного path. Если менять файлы не нужно, верни {\"changes\":[]}."
    )
