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
    for raw in raw_changes:
        if not isinstance(raw, dict):
            raise ValidationError("Некорректный элемент changes")
        path = str(raw.get("path") or "").replace("\\", "/").strip().lstrip("/")
        operation = str(raw.get("operation") or "update").strip().lower()
        content = raw.get("content")
        if operation not in {"create", "update"}:
            raise ValidationError("Dev Studio пока разрешает только create/update")
        if not path or path.startswith(".") or ".." in path.split("/") or "\x00" in path:
            raise ValidationError("Developer предложил небезопасный путь файла")
        if not isinstance(content, str):
            raise ValidationError("Для каждого изменения требуется полный текст content")
        total += len(content)
        if total > MAX_CONTENT_CHARS:
            raise ValidationError("Суммарный объём предложенных изменений слишком большой")
        changes.append(
            {
                "path": path,
                "operation": operation,
                "content": content,
                "reason": str(raw.get("reason") or "").strip()[:500],
            }
        )
    return changes


def developer_output_contract():
    return (
        "Если нужны изменения кода, в самом конце ответа ОБЯЗАТЕЛЬНО добавь JSON-блок строго такого формата: "
        "{\"changes\":[{\"path\":\"relative/path.py\",\"operation\":\"update\",\"content\":\"ПОЛНЫЙ новый текст файла\",\"reason\":\"зачем\"}]}. "
        "Для нового файла используй operation=create. Не предлагай delete. Если менять файлы не нужно, верни "
        "{\"changes\":[]}. JSON должен быть валидным и content должен содержать полный итоговый файл, а не diff."
    )
