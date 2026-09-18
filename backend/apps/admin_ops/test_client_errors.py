import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User

from .issue_models import SystemIssue
from .system_health import INDEX_KEY, list_issues


@pytest.mark.django_db
def test_authenticated_client_error_is_registered_without_request_body():
    cache.delete(INDEX_KEY)
    user = User.objects.create_user(
        username="frontend-error-user",
        email="frontend-error@example.test",
        password="password123!",
    )
    client = APIClient()
    client.force_authenticate(user)
    response = client.post(
        "/api/v1/client-errors/",
        {
            "error_name": "TypeError",
            "message": "Cannot read property",
            "stack": "TypeError: Cannot read property\n at Workspace",
            "source_path": "/app",
        },
        format="json",
    )
    assert response.status_code == 202
    issue = list_issues()[0]
    assert issue["exception_type"] == "Frontend:TypeError"
    assert issue["path"] == "frontend:/app"
    assert issue["user_id"] == str(user.id)


@pytest.mark.django_db
def test_anonymous_client_error_is_registered_without_user_data():
    cache.delete(INDEX_KEY)
    response = APIClient().post(
        "/api/v1/client-errors/",
        {
            "error_name": "TypeError",
            "message": "Registration UI failed",
            "stack": "TypeError: Registration UI failed\n at RegisterPage",
            "source_path": "/register",
        },
        format="json",
    )
    assert response.status_code == 202
    issue = SystemIssue.objects.get(source="frontend:/register")
    assert issue.exception_type == "Frontend:TypeError"
    assert issue.user_reference == ""
    assert issue.occurrences == 1
