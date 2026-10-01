from __future__ import annotations

import sys

from .models import Generation


def _durable_file_rows(file_ids):
    return [{"file_id": str(item)} for item in (file_ids or [])]


def _persist_failed_request_files(*, user, idempotency_key, file_ids):
    ids = [str(item) for item in (file_ids or [])]
    if not ids:
        return
    generation = Generation.objects.filter(
        owner=user,
        idempotency_key=idempotency_key,
    ).only("id", "context_snapshot").first()
    if generation is None:
        return
    snapshot = dict(generation.context_snapshot or {})
    # A later successful prepare stores richer attachment metadata. Never overwrite
    # it. This fallback exists only for the narrow window after durable Generation
    # creation and before context assembly/routing/search/billing finishes.
    existing = snapshot.get("attached_files")
    if existing:
        return
    snapshot["attached_files"] = _durable_file_rows(ids)
    snapshot.setdefault("vision_assets", [])
    Generation.objects.filter(pk=generation.pk).update(context_snapshot=snapshot)


def install(streaming_module) -> None:
    """Preserve exact attachment identity when prepare fails after DB acceptance.

    ``resolve_chat_attachments`` runs before Generation creation, therefore the
    existence of a Generation proves the submitted file_ids already passed tenant,
    project, deletion and readiness validation once. Persisting only their UUIDs is
    enough for idempotency/retry; every future execution re-runs the normal resolver
    and cannot use a deleted or no-longer-authorized file.
    """
    raw_prepare = streaming_module.prepare
    if getattr(raw_prepare, "_ai_workspace_attachment_durability", False):
        return

    def prepare(*args, **kwargs):
        try:
            return raw_prepare(*args, **kwargs)
        except BaseException:
            try:
                _persist_failed_request_files(
                    user=kwargs.get("user"),
                    idempotency_key=kwargs.get("idempotency_key"),
                    file_ids=kwargs.get("file_ids") or [],
                )
            except Exception:
                # Never replace the original prepare exception with a best-effort
                # durability error. Preflight terminalization remains authoritative.
                pass
            raise

    prepare._ai_workspace_attachment_durability = True
    prepare._raw_prepare = raw_prepare
    streaming_module.prepare = prepare

    for module_name in (
        "apps.chat.services",
        "apps.chat.views",
        "apps.chat.cost_views",
        "apps.chat.activity_stream",
        "apps.chat.message_actions",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "prepare"):
            module.prepare = prepare
