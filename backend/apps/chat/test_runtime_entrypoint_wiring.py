from django.urls import resolve

from apps.chat import activity_stream, asgi_stream, cost_views, managed_stream, services, streaming, views
from apps.chat.cost_views import ConfirmedConversationStreamView


def _chain_has_marker(callable_obj, marker, raw_attr="_raw_run"):
    current = callable_obj
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, marker, False):
            return True
        current = getattr(current, raw_attr, None)
    return False


def test_all_chat_entrypoints_use_final_terminal_recovery_runtime():
    assert services.run is streaming.run
    assert managed_stream.run is streaming.run
    assert _chain_has_marker(streaming.run, "_ai_workspace_terminal_recovery")
    assert _chain_has_marker(streaming.run, "_ai_workspace_procurement_execution")
    assert _chain_has_marker(streaming.run, "_ai_workspace_delivery_checkpoint_context")
    assert getattr(streaming.run, "_raw_run", None) is not None
    assert getattr(streaming.provider_available, "_ai_workspace_procurement_execution", False) is True
    assert getattr(streaming._snapshot_capacity, "_ai_workspace_procurement_execution", False) is True
    assert _chain_has_marker(
        streaming.adapter_for,
        "_ai_workspace_delivery_checkpoint",
        raw_attr="_raw_adapter_for",
    )


def test_all_prepare_entrypoints_use_terminal_safe_single_flight_runtime():
    assert services.prepare is streaming.prepare
    assert views.prepare is streaming.prepare
    assert cost_views.prepare is streaming.prepare
    assert activity_stream.prepare is streaming.prepare
    assert _chain_has_marker(
        streaming.prepare,
        "_ai_workspace_preflight_terminal",
        raw_attr="_raw_prepare",
    )
    assert _chain_has_marker(
        streaming.prepare,
        "_ai_workspace_single_flight",
        raw_attr="_raw_prepare",
    )
    assert getattr(streaming.prepare, "_raw_prepare", None) is not None


def test_registered_customer_stream_route_uses_confirmed_stream_view():
    match = resolve(
        "/api/v1/conversations/00000000-0000-0000-0000-000000000001/messages/stream/"
    )
    assert match.func.view_class is ConfirmedConversationStreamView


def test_confirmed_stream_view_is_wired_to_native_asgi_runtime():
    assert cost_views.managed_run_async is asgi_stream.managed_run_async
    assert cost_views.follow_generation_async is asgi_stream.follow_generation_async
    assert cost_views.managed_run is managed_stream.managed_run
