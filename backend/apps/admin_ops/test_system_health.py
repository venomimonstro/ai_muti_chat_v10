import pytest
from django.core.cache import cache
from django.test import RequestFactory

from apps.accounts.models import User

from .issue_models import SystemIssue
from .system_health import (
    INDEX_KEY,
    list_issues,
    record_background_exception,
    record_exception,
    system_analysis,
    update_issue,
)


@pytest.mark.django_db
def test_http_exception_is_grouped_persisted_and_reopened_on_recurrence():
    cache.delete(INDEX_KEY)
    request = RequestFactory().get("/api/v1/example/")
    request.user = User.objects.create_user(
        username="issue-user",
        email="issue@example.test",
        password="password123!",
    )
    request.correlation_id = "b5a7420c-8f1a-4f22-9eb5-7ba6722cb345"

    for _ in range(2):
        try:
            raise RuntimeError("synthetic failure")
        except RuntimeError as exc:
            issue = record_exception(request, exc)

    rows = list_issues()
    assert len(rows) == 1
    assert rows[0]["fingerprint"] == issue["fingerprint"]
    assert rows[0]["occurrences"] == 2
    assert rows[0]["path"] == "/api/v1/example/"
    assert rows[0]["correlation_id"] == request.correlation_id
    stored = SystemIssue.objects.get(pk=issue["fingerprint"])
    assert stored.occurrences == 2

    resolved = update_issue(
        issue["fingerprint"],
        status="resolved",
        resolution_note="Исправлено тестом",
    )
    assert resolved["status"] == "resolved"
    assert list_issues(status="open") == []

    try:
        raise RuntimeError("synthetic failure")
    except RuntimeError as exc:
        reopened = record_exception(request, exc)
    assert reopened["status"] == "open"
    assert reopened["occurrences"] == 3
    stored.refresh_from_db()
    assert stored.status == SystemIssue.Status.OPEN
    assert stored.occurrences == 3


@pytest.mark.django_db
def test_background_failure_is_visible_in_analysis():
    cache.delete(INDEX_KEY)
    try:
        raise ValueError("worker failed")
    except ValueError as exc:
        record_background_exception(task_name="files.extract", task_id="task-123", exc=exc)

    result = system_analysis()
    assert result["issues"]["open"] == 1
    issue = result["issues"]["recent"][0]
    assert issue["path"] == "celery:files.extract"
    assert issue["task_id"] == "task-123"
    assert result["state"] in {"healthy", "warning", "critical"}
