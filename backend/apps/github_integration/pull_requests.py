from django.core.exceptions import ValidationError

from .services import GITHUB_API, _headers, _json_request, installation_token


def _token(binding, *, write=False):
    permissions = {"pull_requests": "write", "contents": "read"}
    if write:
        permissions["contents"] = "write"
    return installation_token(
        binding.installation.installation_id,
        repository_ids=[binding.repository_id],
        permissions=permissions,
    )


def create_pull_request(binding, *, head, title, body="", base=None):
    head = str(head or "").strip()
    base = str(base or binding.default_branch).strip()
    if not head or head == base:
        raise ValidationError("Pull Request требует отдельную рабочую ветку")
    if base != binding.default_branch:
        raise ValidationError("Dev Studio может создавать PR только в default branch проекта")
    token = _token(binding)
    payload = _json_request(
        "POST",
        f"{GITHUB_API}/repos/{binding.full_name}/pulls",
        headers=_headers(token),
        json={
            "title": str(title or "AI Workspace changes")[:240],
            "head": head,
            "base": base,
            "body": str(body or "")[:60000],
            "draft": False,
        },
    )
    number = int(payload.get("number") or 0)
    html_url = str(payload.get("html_url") or "")
    head_sha = str(((payload.get("head") or {}).get("sha")) or "")
    if number <= 0 or not head_sha:
        raise ValidationError("GitHub создал некорректный Pull Request")
    return {
        "number": number,
        "html_url": html_url,
        "head": head,
        "head_sha": head_sha,
        "base": base,
        "state": str(payload.get("state") or "open"),
    }


def get_pull_request(binding, number):
    token = _token(binding)
    payload = _json_request(
        "GET",
        f"{GITHUB_API}/repos/{binding.full_name}/pulls/{int(number)}",
        headers=_headers(token),
    )
    return payload


def merge_pull_request(binding, *, number, expected_head, expected_head_sha, merge_method="squash"):
    payload = get_pull_request(binding, number)
    state = str(payload.get("state") or "")
    head = str(((payload.get("head") or {}).get("ref")) or "")
    head_sha = str(((payload.get("head") or {}).get("sha")) or "")
    base = str(((payload.get("base") or {}).get("ref")) or "")
    if state != "open":
        raise ValidationError("Pull Request уже закрыт или объединён")
    if head != str(expected_head or "") or head_sha != str(expected_head_sha or ""):
        raise ValidationError("Pull Request изменился после подтверждения; merge заблокирован")
    if base != binding.default_branch:
        raise ValidationError("Целевая ветка Pull Request изменилась; merge заблокирован")
    if merge_method not in {"merge", "squash", "rebase"}:
        raise ValidationError("Неподдерживаемый merge method")

    token = _token(binding, write=True)
    result = _json_request(
        "PUT",
        f"{GITHUB_API}/repos/{binding.full_name}/pulls/{int(number)}/merge",
        headers=_headers(token),
        json={"sha": expected_head_sha, "merge_method": merge_method},
    )
    if not bool(result.get("merged")):
        raise ValidationError(str(result.get("message") or "GitHub не выполнил merge"))
    return {
        "merged": True,
        "sha": str(result.get("sha") or ""),
        "message": str(result.get("message") or ""),
        "number": int(number),
    }
