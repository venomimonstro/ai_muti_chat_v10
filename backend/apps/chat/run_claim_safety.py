from __future__ import annotations

import json
import sys


FOLLOWER_MARKER = "_ai_workspace_follow_existing_generation"


def _generation_in_progress(chunk) -> bool:
    if not isinstance(chunk, str) or not chunk.startswith("event: error"):
        return False
    for line in chunk.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return str((payload or {}).get("code") or "") == "generation_in_progress"
    return False


def install(streaming_module, managed_stream_module) -> None:
    """Protect the active producer from a simultaneous reconnect/follower.

    The durable streaming runtime uses an atomic QUEUED -> RUNNING claim. A second
    connection can race after it observed QUEUED but before the first producer updates
    its local state. The core run correctly emits ``generation_in_progress`` for that
    loser. Without this guard managed_stream would then classify the intentionally
    empty follower run as ``stream_incomplete`` and could terminalize the *other*
    producer's active Generation.
    """

    raw_run = streaming_module.run
    if getattr(raw_run, "_ai_workspace_run_claim_safety", False) is True:
        return
    raw_finalize = managed_stream_module._finalize_incomplete_stream

    def run(generation, *args, **kwargs):
        for chunk in raw_run(generation, *args, **kwargs):
            if _generation_in_progress(chunk):
                setattr(generation, FOLLOWER_MARKER, True)
            yield chunk

    def finalize_incomplete(generation):
        if bool(getattr(generation, FOLLOWER_MARKER, False)):
            return None
        return raw_finalize(generation)

    run._ai_workspace_run_claim_safety = True
    run._raw_run = raw_run
    # Preserve feature markers consumed by startup/regression checks even though this
    # is now the outermost transport-safety wrapper.
    for marker in (
        "_ai_workspace_terminal_recovery",
        "_ai_workspace_error_contract",
        "_ai_workspace_cooperative_cancel",
        "_ai_workspace_procurement_execution",
    ):
        if getattr(raw_run, marker, False) is True:
            setattr(run, marker, True)

    finalize_incomplete._ai_workspace_run_claim_safety = True
    finalize_incomplete._raw_finalize = raw_finalize

    streaming_module.run = run
    managed_stream_module.run = run
    managed_stream_module._finalize_incomplete_stream = finalize_incomplete

    for module_name in ("apps.chat.services", "apps.chat.views"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "run"):
            module.run = run
