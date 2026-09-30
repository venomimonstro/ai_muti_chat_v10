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
    "pytest.ini",
    "setup.cfg",
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
    "backend": ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "pytest.ini", "manage.py", "Dockerfile"),
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


def _candidate_kind(item):
    path = str(item.get("path") or "")
    if not path or item.get("type") != "file":
        return ""
    name = path.rsplit("/", 1)[-1]
    if name in ROOT_CANDIDATES:
        return "manifest"
    if name in {candidate for values in NESTED_CANDIDATES.values() for candidate in values}:
        return "manifest"
    if name.lower().endswith(SOURCE_SUFFIXES):
        return "source"
    return ""


def _is_candidate_file(item):
    if not _candidate_kind(item):
        return False
    return int(item.get("size") or 0) <= MAX_CANDIDATE_BYTES


def _scan_repository(binding, target_ref):
    tool_calls = 1
    root = list_repository_directory(binding, "", ref=target_ref)
    root_items = root.get("items") or []
    all_items = list(root_items)
    directories = deque()
    scan_truncated = False
    for item in root_items:
        if item.get("type") == "dir" and str(item.get("name") or "") not in IGNORED_DIRECTORIES:
            directories.append((str(item.get("path") or ""), 1))

    scanned = 0
    while directories and scanned < MAX_SCAN_DIRECTORIES:
        path, depth = directories.popleft()
        if not path:
            continue
        if depth > MAX_DEPTH:
            scan_truncated = True
            continue
        try:
            payload = list_repository_directory(binding, path, ref=target_ref)
            tool_calls += 1
        except ValidationError:
            scan_truncated = True
            continue
        scanned += 1
        items = payload.get("items") or []
        all_items.extend(items)
        child_directories = [
            item
            for item in items
            if item.get("type") == "dir"
            and str(item.get("name") or "") not in IGNORED_DIRECTORIES
            and str(item.get("path") or "")
        ]
        if depth >= MAX_DEPTH:
            if child_directories:
                scan_truncated = True
            continue
        for item in child_directories:
            directories.append((str(item.get("path") or ""), depth + 1))
    if directories:
        scan_truncated = True
    return root_items, all_items, tool_calls, not scan_truncated, scanned


def _project_checks(lower_paths, snapshot_complete, has_pytest_contract):
    if not snapshot_complete:
        return []
    checks = []
    if "manage.py" in lower_paths:
        checks.append("django-check")
    elif "backend/manage.py" in lower_paths:
        checks.append("django-check-backend")
    if has_pytest_contract:
        checks.append("pytest-backend" if "backend/manage.py" in lower_paths else "pytest")
    return checks


def _workspace_profile(
    *,
    discovered,
    candidate_paths,
    files,
    scan_complete,
    content_complete,
    oversized_candidates,
):
    discovered_paths = {str(item.get("path") or "") for item in discovered}
    file_paths = {str(item.get("path") or "") for item in files}
    lower_paths = {path.casefold() for path in discovered_paths}
    python_project = any(path.endswith(".py") for path in lower_paths) or any(
        path in lower_paths for path in {"pyproject.toml", "requirements.txt", "backend/requirements.txt"}
    )
    django_project = "manage.py" in lower_paths or "backend/manage.py" in lower_paths
    node_project = "package.json" in lower_paths or "frontend/package.json" in lower_paths
    has_pytest_contract = any(
        path in lower_paths
        for path in {"pytest.ini", "pyproject.toml", "setup.cfg", "backend/pytest.ini", "backend/pyproject.toml"}
    )
    selected_all_candidates = len(file_paths) == len(candidate_paths)
    snapshot_complete = bool(
        scan_complete
        and content_complete
        and selected_all_candidates
        and not oversized_candidates
    )
    if snapshot_complete:
        evidence_level = "complete_bounded_snapshot"
    elif scan_complete:
        evidence_level = "partial_content_snapshot"
    else:
        evidence_level = "bounded_discovery_snapshot"
    checks = _project_checks(lower_paths, snapshot_complete, has_pytest_contract)
    return {
        "scan_complete": bool(scan_complete),
        "content_complete": bool(content_complete),
        "snapshot_complete": snapshot_complete,
        "evidence_level": evidence_level,
        "candidate_count": len(candidate_paths),
        "context_file_count": len(files),
        "oversized_candidates": list(sorted(oversized_candidates))[:20],
        "python_project": python_project,
        "django_project": django_project,
        "node_project": node_project,
        "pytest_contract": has_pytest_contract,
        "project_checks_available": checks,
    }


def build_repository_context(project, *, ref=None):
    try:
        binding = project.github_repository
    except Exception as exc:
        raise ValidationError("Dev Studio project has no GitHub repository binding") from exc

    target_ref = str(ref or binding.default_branch).strip()
    root_items, discovered, tool_calls, scan_complete, scanned_directories = _scan_repository(binding, target_ref)
    tree_lines = [
        f"{item.get('type', '?')}: {item.get('path', item.get('name', ''))}"
        for item in discovered[:500]
    ]

    candidates = {}
    oversized_candidates = set()
    for item in discovered:
        kind = _candidate_kind(item)
        if not kind:
            continue
        path = str(item.get("path") or "")
        if int(item.get("size") or 0) > MAX_CANDIDATE_BYTES:
            if path:
                oversized_candidates.add(path)
            continue
        if path:
            candidates[path] = item

    selected = sorted(candidates, key=_priority)[: max(1, MAX_FILES)]
    files = []
    remaining = max(1, MAX_CONTEXT_CHARS)
    content_complete = len(selected) == len(candidates)
    for path in selected:
        if remaining <= 0:
            content_complete = False
            break
        try:
            payload = read_repository_file(binding, path, ref=target_ref)
            tool_calls += 1
        except ValidationError:
            content_complete = False
            continue
        raw_content = str(payload.get("content") or "")
        content = _clip(raw_content, remaining)
        if not content:
            content_complete = False
            continue
        if len(content) < len(raw_content):
            content_complete = False
        files.append({"path": path, "content": content, "sha": payload.get("sha")})
        remaining -= len(content)

    profile = _workspace_profile(
        discovered=discovered,
        candidate_paths=set(candidates),
        files=files,
        scan_complete=scan_complete,
        content_complete=content_complete,
        oversized_candidates=oversized_candidates,
    )
    rendered = "Repository: " + binding.full_name + "\n"
    rendered += "Ref: " + target_ref + "\n"
    rendered += "Default branch: " + binding.default_branch + "\n"
    rendered += "Write enabled: " + ("yes" if binding.write_enabled else "no") + "\n"
    rendered += (
        f"Discovered entries: {len(discovered)}; context files: {len(files)}; "
        f"evidence: {profile['evidence_level']}\n\n"
    )
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
        "candidate_count": len(candidates),
        "scanned_directories": scanned_directories,
        "workspace_profile": profile,
        "tool_calls": tool_calls,
        "rendered": rendered[:MAX_CONTEXT_CHARS],
    }
