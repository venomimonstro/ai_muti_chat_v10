import inspect

from django.http import StreamingHttpResponse

from apps.chat import activity_stream


def test_public_chat_stream_is_native_async_under_asgi():
    stream_factory = activity_stream.managed_request_stream
    assert getattr(stream_factory, "_ai_workspace_native_asgi", False) is True
    assert inspect.isasyncgenfunction(stream_factory)

    # Calling an async-generator function does not execute routing/DB/provider work.
    # It is therefore safe to validate the exact response contract without fixtures.
    iterator = stream_factory(
        user=object(),
        conversation=object(),
        idempotency_key="asgi-contract",
        payload={},
    )
    response = StreamingHttpResponse(
        iterator,
        content_type="text/event-stream; charset=utf-8",
    )
    assert response.is_async is True
