from rest_framework import serializers


BOOLEAN_TOOLS = {"web", "files", "images", "github", "write_code", "delegate", "approve"}
ENUM_TOOLS = {
    "publish": {"approval", "disabled"},
    "merge": {"approval", "disabled"},
    "shell": {"sandbox", "disabled"},
}
ALLOWED_KEYS = BOOLEAN_TOOLS | set(ENUM_TOOLS)


def validate_tool_policy(value):
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise serializers.ValidationError("Инструменты должны передаваться объектом")
    unknown = sorted(set(value) - ALLOWED_KEYS)
    if unknown:
        raise serializers.ValidationError(
            f"Неизвестные инструменты: {', '.join(unknown)}"
        )
    cleaned = {}
    for key in BOOLEAN_TOOLS:
        if key not in value:
            continue
        raw = value[key]
        if not isinstance(raw, bool):
            raise serializers.ValidationError({key: "Используйте true или false"})
        cleaned[key] = raw
    for key, allowed in ENUM_TOOLS.items():
        if key not in value:
            continue
        raw = str(value[key]).strip().lower()
        if raw not in allowed:
            raise serializers.ValidationError(
                {key: f"Допустимые значения: {', '.join(sorted(allowed))}"}
            )
        cleaned[key] = raw
    return cleaned
