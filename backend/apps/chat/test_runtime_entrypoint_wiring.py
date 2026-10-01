from apps.chat import managed_stream, services, streaming, views


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
