from __future__ import annotations

import hashlib
import json


COMPLEX_MARKERS = (
    "проанализ",
    "аудит",
    "сравни",
    "стратег",
    "архитект",
    "план разработ",
    "найди баг",
    "почему",
    "докажи",
    "рассчитай",
    "экономик",
    "окупаем",
    "риск",
    "вариант",
    "пошаг",
    "техзадан",
    "тз ",
    "код",
    "рефактор",
    "оптимиз",
)

REASONING_CONTRACT = (
    "\n\nРЕЖИМ УГЛУБЛЕННОГО АНАЛИЗА:\n"
    "Задача требует повышенной проверки качества. Перед формулировкой ответа внутренне проверь "
    "ограничения, противоречия, допущения, вычисления и альтернативы. Не раскрывай скрытую цепочку "
    "рассуждений, scratchpad или пошаговые внутренние мысли. Пользователю дай прямой вывод, затем только "
    "краткие проверяемые основания, существенные допущения/риски и практический следующий шаг. Если данных "
    "недостаточно, явно отдели подтверждённое от предположения."
)


def reasoning_required(query: str) -> bool:
    text = " ".join(str(query or "").casefold().split())
    if not text:
        return False
    score = sum(1 for marker in COMPLEX_MARKERS if marker in text)
    if score >= 1 and len(text) >= 80:
        return True
    if score >= 2:
        return True
    separators = text.count(";") + text.count("\n") + text.count(" 1)") + text.count(" 2)")
    return len(text) >= 500 or separators >= 3


def install(context_module, streaming_module) -> None:
    raw = context_module.assemble_context
    if getattr(raw, "_ai_workspace_adaptive_reasoning", False):
        streaming_module.assemble_context = raw
        return

    def assemble_context(*args, **kwargs):
        payload, memories = raw(*args, **kwargs)
        messages = list(payload.get("provider_messages") or [])
        query = ""
        for message in reversed(messages):
            if str(message.get("role") or "") == "user":
                query = str(message.get("content") or "")
                break
        if not reasoning_required(query) or not messages:
            payload["reasoning_mode"] = "standard"
            return payload, memories

        system_index = next(
            (index for index, item in enumerate(messages) if item.get("role") == "system"),
            None,
        )
        if system_index is None:
            payload["reasoning_mode"] = "standard"
            return payload, memories

        extra_tokens = context_module.estimate_tokens(REASONING_CONTRACT)
        remaining = int((payload.get("budget") or {}).get("remaining") or 0)
        if extra_tokens + 8 > remaining:
            payload["reasoning_mode"] = "standard_budget_limited"
            return payload, memories

        messages[system_index] = {
            **messages[system_index],
            "content": str(messages[system_index].get("content") or "") + REASONING_CONTRACT,
        }
        payload["provider_messages"] = messages
        payload["reasoning_mode"] = "deep"
        payload["components"] = list(payload.get("components") or []) + [
            {
                "kind": "reasoning_policy",
                "source_id": "adaptive_reasoning_v1",
                "label": "Углублённая проверка ответа",
                "content": REASONING_CONTRACT.strip(),
                "tokens": extra_tokens,
                "score": 1.0,
                "truncated": False,
            }
        ]
        budget = dict(payload.get("budget") or {})
        budget["input_tokens"] = int(budget.get("input_tokens") or 0) + extra_tokens
        budget["remaining"] = max(0, int(budget.get("remaining") or 0) - extra_tokens)
        payload["budget"] = budget
        payload["sha256"] = hashlib.sha256(
            json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        return payload, memories

    assemble_context._ai_workspace_adaptive_reasoning = True
    assemble_context._raw_assemble_context = raw
    context_module.assemble_context = assemble_context
    # streaming imports assemble_context by value before AppConfig.ready(). Rebind
    # the exact runtime symbol so the customer path receives the policy too.
    streaming_module.assemble_context = assemble_context
