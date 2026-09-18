import pytest
from rest_framework.test import APIClient

from apps.accounts.models import SupportRequest, User


@pytest.mark.django_db
def test_client_support_request_persists_category_and_is_user_scoped():
    alice = User.objects.create_user(
        username="support-alice",
        email="support-alice@example.test",
        password="test-password-123",
    )
    bob = User.objects.create_user(
        username="support-bob",
        email="support-bob@example.test",
        password="test-password-123",
    )
    SupportRequest.objects.create(
        user=bob,
        subject="Чужое обращение",
        category=SupportRequest.Category.BILLING,
        message="Не должно быть видно",
    )
    client = APIClient()
    client.force_authenticate(alice)

    created = client.post(
        "/api/v1/auth/support/",
        {
            "subject": "Не загрузился ответ",
            "category": "generation",
            "message": "Ответ остановился после нескольких строк",
        },
        format="json",
    )
    assert created.status_code == 201
    assert created.data["category"] == "generation"
    saved = SupportRequest.objects.get(pk=created.data["id"])
    assert saved.user == alice
    assert saved.category == SupportRequest.Category.GENERATION

    listed = client.get("/api/v1/auth/support/")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.data] == [str(saved.id)]
