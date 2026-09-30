import os
from collections import deque

from django.core.exceptions import ValidationError

from apps.github_integration.services import list_repository_directory, read_repository_file


MAX_CONTEXT_CHARS = int(os.getenv("DEV_CONTEXT_MAX_CHARS", "120000"))
MAX_FILES = int(os.getenv("DEV_CONTEXT_MAX_FILES", "32"))
MAX_SCAN_DIRECTORIES = int(os.getenv("DEV_CONTEXT_MAX_SCAN_DIRECTORIES", "24"))
MAX_DEPTH = int(os.getenv("DEV_CONTEXT_MAX_DEPTH", "2"))
MAX_CANDIDATE_BYTES = int(os.getenv("DEV_CONTEXT_MAX_CANDIDATE_BYTES", str(512 * 1024)))

ROOT_CANDIDATES = (
    "README.md",
    "README.rst",
    "README.txt",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "tsconfig.json",
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
    "frontend": ("package.json", "tsconfig.json", "next.config.js", "next.config.mjs", "next.config.ts", "Dockerfile"),
}
IGNORED_DIRECTORIES = {
    ".git",
    ".github",
    ".idea",
    ".next",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "media",
    "node_modules",
    "staticfiles",
    "vendor",
    "venv",
}
SOURCE_SUFFIXES = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".php",
    ".rb",
    ".cs",
    ".sql",
    ".graphql",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
)


def _clip(text, remaining):
    if remaining <= 0:
        return ""
    return text[:remaining]


def _priority(path):
    normalized = str(path or "").replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    depth = normalized.count("/")
    if normalized in ROOT_CANDIDATES:
        return (0, ROOT_CANDIDATES.index(normalized), normalized.casefold())
    for directory, candidates in NESTED_CANDIDATES.items():
        if normalized.startswith(f"{directory}/") and name in candidates:
            return (1, candidates.index(name), normalized.casefold())
    if name.casefold().startswith("readme"):
        return (2, depth, normalized.casefold())
    if name in {"urls.py", "settings.py", "models.py", "views.py", "tasks.py", "page.tsx", "layout.tsx", "route.ts"}:
        return (3, depth, normalized.casefold())
    return (4, depth, normalized.casefold())


def _is_candidate_file(item):
    path = str(item.get("path") or "")
    if not path or item.get("type") != "file":
        return False
    if int(item.get("size") or 0) > MAX_CANDIDATE_BYTES:
        return False
    name = path.rsplit("/", 1)[-1]
    if name in ROOT_CANDIDATES:
        return True
    if name in {candidate for values in NESTED_CANDIDATES.values() for candidate in values}:
        return True
    return name.lower().endswith(SOURCE_SUFFIXES)


def _scan_repository(binding, target_ref):
    tool_calls = 1
    root = list_repository_directory(binding, "", ref=target_ref)
    root_items = root.get("items") or []
    all_items = list(root_items)
    directories = deque()
    for item in root_items:
        if item.get("type") == "dir" and str(item.get("name") or "") not in IGNORED_DIRECTORIES:
            directories.append((str(item.get("path") or ""), 1))

    scanned = 0
    while directories and scanned < MAX_SCAN_DIRECTORIES:
        path, depth = directories.popleft()
        if not path or depth > MAX_DEPTH:
            continue
        try:
            payload = list_repository_directory(binding, path, ref=target_ref)
            tool_calls += 1
        except ValidationError:
            continue
        scanned += 1
        items = payload.get("items") or []
        all_items.extend(items)
        if depth >= MAX_DEPTH:
            continue
        for item in items:
            if item.get("type") != "dir":
                continue
            name = str(item.get("name") or "")
            child_path = str(item.get("path") or "")
            if name in IGNORED_DIRECTORIES or not child_path:
                continue
            directories.append((child_path, depth + 1))
    return root_items, all_items, tool_calls


def build_repository_context(project, *, ref=None):
    try:
        binding = project.github_repository
    except Exception as exc:
        raise ValidationError("Dev Studio project has no GitHub repository binding") from exc

    target_ref = str(ref or binding.default_branch).strip()
    root_items, discovered, tool_calls = _scan_repository(binding, target_ref)
    tree_lines = [
        f"{item.get('type', '?')}: {item.get('path', item.get('name', ''))}"
        for item in discovered[:500]
    ]

    candidates = {}
    for item in discovered:
        if not _is_candidate_file(item):
            continue
        path = str(item.get("path") or "")
        if path:
            candidates[path] = item

    selected = sorted(candidates, key=_priority)[: max(1, MAX_FILES)]
    files = []
    remaining = max(1, MAX_CONTEXT_CHARS)
    for path in selected:
        if remaining <= 0:
            break
        try:
            payload = read_repository_file(binding, path, ref=target_ref)
            tool_calls += 1
        except ValidationError:
            continue
        content = _clip(str(payload.get("content") or ""), remaining)
        if not content:
            continue
        files.append({"path": path, "content": content, "sha": payload.get("sha")})
        remaining -= len(content)

    rendered = "Repository: " + binding.full_name + "\n"
    rendered += "Ref: " + target_ref + "\n"
    rendered += "Default branch: " + binding.default_branch + "\n"
    rendered += "Write enabled: " + ("yes" if binding.write_enabled else "no") + "\n"
    rendered += f"Discovered entries: {len(discovered)}; context files: {len(files)}\n\n"
    rendered += "Repository tree (bounded scan):\n" + "\n".join(tree_lines)
    for item in files:
        rendered += f"\n\n--- {item['path']} ---\n{item['content']}"
    return {
        "repository": binding.full_name,
        "ref": target_ref,
        "default_branch": binding.default_branch,
        "write_enabled": binding.write_enabled,
        "tree": tree_lines,
        "files": files,
        "discovered_count": len(discovered),
        "tool_calls": tool_calls,
        "rendered": rendered[:MAX_CONTEXT_CHARS],
    }
