import re

from django.core.exceptions import ValidationError

from .services import GITHUB_API, _headers, _json_request, installation_token

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")


def _safe_branch(value):
    branch = str(value or "").strip().strip("/")
    if (
        not branch
        or not _BRANCH_RE.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or branch.endswith(".")
        or branch.endswith(".lock")
        or "@{" in branch
    ):
        raise ValidationError("Некорректное имя GitHub-ветки")
    return branch


def create_repository_branch(binding, branch, *, from_ref=None):
    if not binding.write_enabled:
        raise ValidationError("GitHub write access is disabled for this project")
    target = _safe_branch(branch)
    base = _safe_branch(from_ref or binding.default_branch)
    if target == binding.default_branch:
        raise ValidationError("Рабочая ветка не может совпадать с default branch")
    token = installation_token(
        binding.installation.installation_id,
        repository_ids=[binding.repository_id],
        permissions={"contents": "write"},
    )
    base_payload = _json_request(
        "GET",
        f"{GITHUB_API}/repos/{binding.full_name}/git/ref/heads/{base}",
        headers=_headers(token),
    )
    sha = str((base_payload.get("object") or {}).get("sha") or "")
    if not sha:
        raise ValidationError("Не удалось определить commit исходной ветки")
    payload = _json_request(
        "POST",
        f"{GITHUB_API}/repos/{binding.full_name}/git/refs",
        headers=_headers(token),
        json={"ref": f"refs/heads/{target}", "sha": sha},
    )
    return {
        "branch": target,
        "base_branch": base,
        "sha": str((payload.get("object") or {}).get("sha") or sha),
    }
