from apps.chat import asgi_entrypoint, managed_stream, services, streaming, views


def test_all_chat_entrypoints_use_final_terminal_recovery_runtime():
    assert services.run is streaming.run
    assert managed_stream.run is streaming.run
    assert getattr(streaming.run, "_ai_workspace_terminal_recovery", False) is True
    assert getattr(streaming.run, "_raw_run", None) is not None


def test_all_prepare_entrypoints_use_terminal_safe_preflight_runtime():
    assert services.prepare is streaming.prepare
    assert views.prepare is streaming.prepare
    assert getattr(streaming.prepare, "_ai_workspace_preflight_terminal", False) is True
    assert getattr(streaming.prepare, "_raw_prepare", None) is not None


def test_customer_stream_endpoint_uses_native_asgi_transport():
    endpoint = views.ConversationViewSet.stream_messages
    assert getattr(endpoint, "_ai_workspace_native_asgi_stream", False) is True
    assert getattr(endpoint, "_raw_stream_messages", None) is not None


def test_asgi_request_detection_is_explicit_not_environment_guessing():
    class ASGIRequest:
        scope = {"type": "http"}

    class Wrapper:
        _request = ASGIRequest()

    class WSGIRequest:
        pass

    class WSGIWrapper:
        _request = WSGIRequest()

    assert asgi_entrypoint._is_asgi_request(Wrapper()) is True
    assert asgi_entrypoint._is_asgi_request(WSGIWrapper()) is False
