from __future__ import annotations

import hashlib
import json


TRUSTED_SYSTEM_KINDS = {
    "system_policy",
    "product_identity",
    "project_instruction",
}
RECENT_KIND = "recent_message"


def _join(contents):
    return "\n\n".join(str(item or "").strip() for item in contents if str(item or "").strip())


def _message_tokens(context_module, messages):
    return sum(context_module.estimate_tokens(str(item.get("content") or "")) + 4 for item in messages)


def _rebuild_payload(context_module, payload):
    components = list(payload.get("components") or [])
    raw_messages = list(payload.get("provider_messages") or [])
    reference_components = [item for item in components if item.get("kind") != RECENT_KIND]
    trusted = [item for item in reference_components if item.get("kind") in TRUSTED_SYSTEM_KINDS]
    # Strict allowlist: memory, summaries, files, retrieved old messages and every
    # future/unknown context kind are data, never system instructions.
    untrusted = [item for item in reference_components if item.get("kind") not in TRUSTED_SYSTEM_KINDS]

    has_reference = bool(reference_components)
    recent_messages = raw_messages[1:] if has_reference and raw_messages else raw_messages
    messages = []
    trusted_text = _join(item.get("content") for item in trusted)
    if trusted_text:
        messages.append({"role": "system", "content": trusted_text})
    untrusted_text = _join(item.get("content") for item in untrusted)
    if untrusted_text:
        messages.append({"role": "user", "content": untrusted_text})
    messages.extend(recent_messages)

    input_limit = int((payload.get("budget") or {}).get("input_limit") or 0)
    if input_limit and _message_tokens(context_module, messages) > input_limit and untrusted_text:
        overflow = _message_tokens(context_module, messages) - input_limit
        current_tokens = context_module.estimate_tokens(untrusted_text)
        allowed = max(0, current_tokens - overflow - 4)
        trimmed, was_truncated = context_module._trim_tokens(untrusted_text, allowed)
        if trimmed:
            untrusted_index = 1 if trusted_text else 0
            messages[untrusted_index] = {"role": "user", "content": trimmed}
        else:
            messages = [item for item in messages if item.get("content") != untrusted_text]
        if was_truncated:
            payload["context_safety_truncated"] = True

    payload["provider_messages"] = messages
    input_tokens = _message_tokens(context_module, messages)
    if "budget" in payload:
        payload["budget"]["input_tokens"] = input_tokens
        payload["budget"]["remaining"] = max(0, input_limit - input_tokens) if input_limit else 0
    payload["sha256"] = hashlib.sha256(
        json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    payload["context_trust"] = {
        "trusted_system_kinds": sorted(TRUSTED_SYSTEM_KINDS),
        "untrusted_reference_count": len(untrusted),
    }
    return payload


def install(*, context_module, streaming_module) -> None:
    raw = context_module.assemble_context
    if getattr(raw, "_ai_workspace_context_safety", False):
        streaming_module.assemble_context = raw
        return

    def assemble_context(*args, **kwargs):
        payload, memory_items = raw(*args, **kwargs)
        return _rebuild_payload(context_module, payload), memory_items

    assemble_context._ai_workspace_context_safety = True
    assemble_context._raw_assemble_context = raw
    context_module.assemble_context = assemble_context
    # streaming imported assemble_context by value, so explicitly rebind it.
    streaming_module.assemble_context = assemble_context
