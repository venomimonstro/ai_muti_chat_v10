from __future__ import annotations

import sys


PREPARE_MARKERS = (
    "_ai_workspace_customer_capacity",
    "_ai_workspace_conversation_snapshot",
    "_ai_workspace_single_flight",
    "_ai_workspace_preflight_terminal",
    "_ai_workspace_attachment_durability",
)
RUN_MARKERS = (
    "_ai_workspace_runtime_readiness",
    "_ai_workspace_procurement_execution",
    "_ai_workspace_delivery_checkpoint_context",
    "_ai_workspace_cooperative_cancel",
    "_ai_workspace_terminal_recovery",
    "_ai_workspace_error_contract",
    "_ai_workspace_execution_fence_outer",
)


def _expose_chain_markers(callable_obj, *, raw_attr: str, markers) -> None:
    """Expose installed guard markers on the final public callable.

    Chat reliability is assembled from deliberately small wrappers. Some diagnostics
    and release gates inspect the final callable directly while others walk the
    wrapper chain. Copying marker booleans to the final callable keeps both views
    consistent without changing wrapper execution order.
    """
    current = callable_obj
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for marker in markers:
            if getattr(current, marker, False):
                setattr(callable_obj, marker, True)
        current = getattr(current, raw_attr, None)


def _bind(module_name: str, **values) -> None:
    module = sys.modules.get(module_name)
    if module is None:
        return
    for name, value in values.items():
        if hasattr(module, name):
            setattr(module, name, value)


def synchronize(*, streaming_module, managed_stream_module, activity_stream_module, live_tools_module) -> None:
    """Publish one final chat runtime to every HTTP/SSE entrypoint.

    A number of chat modules import prepare/run by value. Runtime safety is then
    installed during AppConfig.ready() as a wrapper chain. Without a final binding
    pass, modules imported early can retain stale pre-guard callables, so one endpoint
    may bypass single-flight, terminal recovery or procurement checks while another
    endpoint works correctly. This function is the single finalization point.
    """
    final_prepare = streaming_module.prepare
    final_run = streaming_module.run

    _expose_chain_markers(
        final_prepare,
        raw_attr="_raw_prepare",
        markers=PREPARE_MARKERS,
    )
    _expose_chain_markers(
        final_run,
        raw_attr="_raw_run",
        markers=RUN_MARKERS,
    )

    for module_name in (
        "apps.chat.services",
        "apps.chat.views",
        "apps.chat.cost_views",
        "apps.chat.activity_stream",
        "apps.chat.message_actions",
    ):
        _bind(module_name, prepare=final_prepare)

    for module_name in (
        "apps.chat.services",
        "apps.chat.views",
        "apps.chat.managed_stream",
    ):
        _bind(module_name, run=final_run)

    # managed_run must itself call the same final run() object. Rebinding the module
    # global closes the common Python "from x import run" stale-reference trap.
    managed_stream_module.run = final_run
    activity_stream_module.managed_run = managed_stream_module.managed_run
    activity_stream_module.prepare = final_prepare
    activity_stream_module.needs_web_search = live_tools_module.needs_web_search

    # Keep helper functions imported by value aligned with the final streaming
    # runtime too. This matters for late readiness, key-scoped health and accounting.
    for module_name in (
        "apps.chat.services",
        "apps.chat.views",
        "apps.chat.managed_stream",
    ):
        values = {}
        for name in ("adapter_for", "record_failure", "record_success", "provider_available"):
            if hasattr(streaming_module, name):
                values[name] = getattr(streaming_module, name)
        _bind(module_name, **values)
