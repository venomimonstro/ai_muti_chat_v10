from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections, connection
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import Conversation, ConversationDraft


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires real PostgreSQL row locks")
def test_concurrent_first_draft_saves_share_one_parent_lock():
    user = User.objects.create_user(username="concurrent-draft", password="password123!")
    conversation = Conversation.objects.create(owner=user)
    start = Barrier(2)

    def save(content):
        close_old_connections()
        try:
            client = APIClient()
            client.force_authenticate(user)
            start.wait(timeout=5)
            response = client.put(
                f"/api/v1/conversations/{conversation.id}/draft/",
                {"content": content},
                format="json",
            )
            return response.status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(save, ["Первый черновик", "Второй черновик"]))
    assert results == [200, 200]
    assert ConversationDraft.objects.filter(conversation=conversation).count() == 1
    assert conversation.draft.content in {"Первый черновик", "Второй черновик"}
