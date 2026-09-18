from decimal import Decimal

import pytest
from django.core.files.base import ContentFile
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.chat.models import Conversation, Generation, Message
from apps.files.models import FileAsset
from apps.image_studio.models import ImageGeneration, ImageModel
from apps.projects.models import Project


@pytest.mark.django_db(transaction=True)
def test_conversation_assets_keep_true_counts_across_tabs_and_do_not_leak_other_user_data(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(
        username="asset-owner", email="asset-owner@example.test", password="password123"
    )
    outsider = User.objects.create_user(
        username="asset-outsider", email="asset-outsider@example.test", password="password123"
    )
    project = Project.objects.create(owner=user, name="Assets project")
    conversation = Conversation.objects.create(owner=user, project=project, title="Assets")
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Документация https://example.com/docs",
    )
    assistant_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="Результат https://docs.example.com/result",
    )

    attached = FileAsset(
        owner=user,
        project=project,
        original_name="report.pdf",
        declared_content_type="application/pdf",
        detected_type="pdf",
        size_bytes=5,
        sha256="a" * 64,
        status=FileAsset.Status.READY,
        scan_status=FileAsset.ScanStatus.BASIC_PASSED,
        idempotency_key="asset:file:owner",
    )
    attached.blob.save("report.pdf", ContentFile(b"hello"), save=True)
    Generation.objects.create(
        owner=user,
        user_message=user_message,
        assistant_message=assistant_message,
        state=Generation.State.COMPLETED,
        model="echo-v1",
        idempotency_key="asset:generation:owner",
        context_snapshot={
            "vision_assets": [{"file_id": str(attached.id), "file_name": attached.original_name}],
            "web_sources": [
                {"url": "https://search.example.com/source", "title": "Поисковый источник"}
            ],
        },
    )

    foreign_project = Project.objects.create(owner=outsider, name="Foreign project")
    foreign = Conversation.objects.create(owner=outsider, project=foreign_project, title="Foreign")
    Message.objects.create(
        conversation=foreign,
        role=Message.Role.USER,
        content="Секрет https://secret.example.test/private",
    )

    provider = Provider.objects.create(slug="asset-image-provider", name="Images")
    model = ImageModel.objects.create(
        provider=provider,
        slug="asset-image-model",
        display_name="Asset image",
        upstream_model="asset-image-v1",
        provider_price_per_image=Decimal("1"),
    )
    ImageGeneration.objects.create(
        owner=user,
        conversation=conversation,
        model=model,
        prompt="Красный автомобиль",
        size="1024x1024",
        quality="standard",
        requested_count=1,
        actual_count=1,
        state=ImageGeneration.State.COMPLETED,
        idempotency_key="assets:image:owner",
        estimated_cost_rub=Decimal("2"),
        actual_cost_rub=Decimal("2"),
    )
    ImageGeneration.objects.create(
        owner=outsider,
        conversation=foreign,
        model=model,
        prompt="Секретное изображение",
        size="1024x1024",
        quality="standard",
        requested_count=1,
        actual_count=1,
        state=ImageGeneration.State.COMPLETED,
        idempotency_key="assets:image:outsider",
        estimated_cost_rub=Decimal("2"),
        actual_cost_rub=Decimal("2"),
    )

    client = APIClient()
    client.force_authenticate(user)
    response = client.get(
        f"/api/v1/conversations/{conversation.id}/assets/?kind=all"
    )

    assert response.status_code == 200
    assert response.data["counts"] == {"images": 1, "files": 1, "links": 3}
    assert len(response.data["images"]) == 1
    assert response.data["images"][0]["prompt"] == "Красный автомобиль"
    assert response.data["files"][0]["original_name"] == "report.pdf"
    urls = {item["url"] for item in response.data["links"]}
    assert urls == {
        "https://example.com/docs",
        "https://docs.example.com/result",
        "https://search.example.com/source",
    }
    assert "secret.example.test" not in str(response.data)

    images_only = client.get(
        f"/api/v1/conversations/{conversation.id}/assets/?kind=images"
    )
    assert images_only.status_code == 200
    assert images_only.data["counts"] == {"images": 1, "files": 1, "links": 3}
    assert len(images_only.data["images"]) == 1
    assert images_only.data["files"] == []
    assert images_only.data["links"] == []

    denied = client.get(f"/api/v1/conversations/{foreign.id}/assets/")
    assert denied.status_code == 404


@pytest.mark.django_db
def test_conversation_assets_search_filters_links_inside_current_chat():
    user = User.objects.create_user(
        username="asset-filter", email="asset-filter@example.test", password="password123"
    )
    conversation = Conversation.objects.create(owner=user, title="Links")
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content="Документация https://docs.python.org/3/ и поиск https://example.com/search",
    )
    client = APIClient()
    client.force_authenticate(user)

    response = client.get(
        f"/api/v1/conversations/{conversation.id}/assets/?kind=links&q=python"
    )
    assert response.status_code == 200
    assert response.data["counts"]["links"] == 1
    assert response.data["links"][0]["url"] == "https://docs.python.org/3/"
