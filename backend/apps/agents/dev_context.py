from django.core.exceptions import ValidationError

from apps.github_integration.services import list_repository_directory, read_repository_file


MAX_CONTEXT_CHARS = 60000
MAX_FILES = 12
ROOT_CANDIDATES = (
    "README.md",
    "README.rst",
    "README.txt",
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker-compose.prod.yml",
    "docker-compose.prod.yaml",
    "compose.yml",
    "compose.yaml",
    "manage.py",
)
NESTED_CANDIDATES = {
    "backend": ("requirements.txt", "pyproject.toml", "manage.py", "Dockerfile"),
    "frontend": ("package.json", "next.config.js", "next.config.mjs", "next.config.ts", "Dockerfile"),
}


def _clip(text, remaining):
    if remaining <= 0:
        return ""
    return text[:remaining]


def build_repository_context(project):
    try:
        binding = project.github_repository
    except Exception as exc:
        raise ValidationError("Dev Studio project has no GitHub repository binding") from exc

    tool_calls = 1
    root = list_repository_directory(binding, "")
    items = root.get("items") or []
    tree_lines = [
        f"{item.get('type', '?')}: {item.get('path', item.get('name', ''))}"
        for item in items[:200]
    ]
    selected = []
    root_names = {str(item.get("name") or ""): item for item in items}
    for name in ROOT_CANDIDATES:
        item = root_names.get(name)
        if item and item.get("type") == "file":
            selected.append(str(item.get("path") or name))

    for directory, candidates in NESTED_CANDIDATES.items():
        entry = root_names.get(directory)
        if not entry or entry.get("type") != "dir":
            continue
        try:
            nested = list_repository_directory(binding, directory)
            tool_calls += 1
        except ValidationError:
            continue
        names = {str(item.get("name") or ""): item for item in nested.get("items") or []}
        for name in candidates:
            item = names.get(name)
            if item and item.get("type") == "file":
                selected.append(str(item.get("path") or f"{directory}/{name}"))

    files = []
    remaining = MAX_CONTEXT_CHARS
    for path in selected[:MAX_FILES]:
        if remaining <= 0:
            break
        try:
            payload = read_repository_file(binding, path)
            tool_calls += 1
        except ValidationError:
            continue
        content = _clip(str(payload.get("content") or ""), remaining)
        if not content:
            continue
        files.append({"path": path, "content": content, "sha": payload.get("sha")})
        remaining -= len(content)

    rendered = "Repository: " + binding.full_name + "\n"
    rendered += "Default branch: " + binding.default_branch + "\n"
    rendered += "Write enabled: " + ("yes" if binding.write_enabled else "no") + "\n\n"
    rendered += "Top-level tree:\n" + "\n".join(tree_lines)
    for item in files:
        rendered += f"\n\n--- {item['path']} ---\n{item['content']}"
    return {
        "repository": binding.full_name,
        "default_branch": binding.default_branch,
        "write_enabled": binding.write_enabled,
        "tree": tree_lines,
        "files": files,
        "tool_calls": tool_calls,
        "rendered": rendered[:MAX_CONTEXT_CHARS],
    }
