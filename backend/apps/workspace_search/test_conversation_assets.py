from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.ai_registry.models import Provider
from apps.chat.models import Conversation, Message
from apps.image_studio.models import ImageGeneration, ImageModel


@pytest.mark.django_db
def test_conversation_assets_keep_true_counts_across_tabs_and_do_not_leak_other_user_data():
    user = User.objects.create_user(
        username="asset-owner", email="asset-owner@example.test", password="password123"
    )
    outsider = User.objects.create_user(
        username="asset-outsider", email="asset-outsider@example.test", password="password123"
    )
    conversation = Conversation.objects.create(owner=user, title="Assets")
    Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content="Документация https://example.com/docs",
    )
    foreign = Conversation.objects.create(owner=outsider, title="Foreign")
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
        f"/api/v1/conversations/{conversation.id}/assets/?kind=images"
    )

    assert response.status_code == 200
    assert response.data["counts"] == {"images": 1, "files": 0, "links": 1}
    assert len(response.data["images"]) == 1
    assert response.data["images"][0]["prompt"] == "Красный автомобиль"
    assert response.data["links"] == []

    links = client.get(
        f"/api/v1/conversations/{conversation.id}/assets/?kind=links"
    )
    assert links.status_code == 200
    assert [item["url"] for item in links.data["links"]] == ["https://example.com/docs"]
    assert "secret.example.test" not in str(links.data)

    denied = client.get(f"/api/v1/conversations/{foreign.id}/assets/")
    assert denied.status_code == 404
