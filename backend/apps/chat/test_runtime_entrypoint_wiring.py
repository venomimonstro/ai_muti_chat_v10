from apps.chat import managed_stream, services, streaming


def test_all_chat_entrypoints_use_final_terminal_recovery_runtime():
    assert services.run is streaming.run
    assert managed_stream.run is streaming.run
    assert getattr(streaming.run, "_ai_workspace_terminal_recovery", False) is True
    assert getattr(streaming.run, "_raw_run", None) is not None
