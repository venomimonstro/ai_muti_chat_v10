from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import AsyncRequestFactory, RequestFactory

from . import cost_views
from .models import Generation


def _generation():
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000111",
        state=Generation.State.QUEUED,
        context_snapshot={"customer_stream_authorized": True},
    )


def test_customer_stream_uses_native_async_runtime_for_asgi_request():
    raw = AsyncRequestFactory().post("/api/v1/conversations/x/messages/stream/", data={})
    request = SimpleNamespace(_request=raw)
    generation = _generation()
    async_sentinel = object()

    with (
        patch.object(cost_views, "managed_run_async", return_value=async_sentinel) as async_run,
        patch.object(cost_views, "managed_run") as sync_run,
    ):
        selected = cost_views._customer_stream(request, generation, created=True)

    assert selected is async_sentinel
    async_run.assert_called_once_with(generation)
    sync_run.assert_not_called()


def test_customer_stream_keeps_sync_fallback_for_wsgi_request():
    raw = RequestFactory().post("/api/v1/conversations/x/messages/stream/", data={})
    request = SimpleNamespace(_request=raw)
    generation = _generation()
    sync_sentinel = object()

    with (
        patch.object(cost_views, "managed_run", return_value=sync_sentinel) as sync_run,
        patch.object(cost_views, "managed_run_async") as async_run,
    ):
        selected = cost_views._customer_stream(request, generation, created=True)

    assert selected is sync_sentinel
    sync_run.assert_called_once_with(generation)
    async_run.assert_not_called()
