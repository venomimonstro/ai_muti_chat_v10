from __future__ import annotations


def install(diagnostics_views_module) -> None:
    """Add the live customer-facing chat snapshot to every diagnostics share.

    Provider health alone is insufficient: a provider may be HEALTHY while every
    model is commercially blocked, quarantined or unfunded. The share link must show
    the same routability/error/stale-operation snapshot that the proactive watchdog
    uses, without exposing message text, credentials or user identity.
    """
    raw_builder = diagnostics_views_module.build_system_diagnostics
    if getattr(raw_builder, "_ai_workspace_chat_service_snapshot", False):
        return

    def build_system_diagnostics():
        payload = raw_builder()
        try:
            from apps.chat.tasks import chat_service_snapshot

            snapshot = chat_service_snapshot()
        except Exception as exc:
            snapshot = {
                "status": "snapshot_failed",
                "error_type": type(exc).__name__,
            }

        payload["schema_version"] = max(int(payload.get("schema_version") or 0), 7)
        payload["chat_service"] = snapshot
        summary = payload.setdefault("summary", {})
        if isinstance(snapshot, dict):
            if "ready_models" in snapshot:
                summary["routable_models_now"] = snapshot["ready_models"]
            if "failure_rate_15m" in snapshot:
                summary["chat_failure_rate_15m"] = snapshot["failure_rate_15m"]
            if "stale_provider_reservations" in snapshot:
                summary["stale_provider_reservations"] = snapshot[
                    "stale_provider_reservations"
                ]
        return payload

    build_system_diagnostics._ai_workspace_chat_service_snapshot = True
    build_system_diagnostics._raw_builder = raw_builder
    diagnostics_views_module.build_system_diagnostics = build_system_diagnostics
