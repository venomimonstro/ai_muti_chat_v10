from apps.chat import activity_stream, live_tools, streaming


def test_activity_stream_uses_final_prepare_runtime():
    assert activity_stream.prepare is streaming.prepare
    assert getattr(streaming.prepare, "_ai_workspace_paid_search_billing", False) is True


def test_activity_stream_uses_final_search_policy():
    assert activity_stream.needs_web_search is live_tools.needs_web_search
    assert getattr(live_tools.needs_web_search, "_ai_workspace_cost_aware_search", False) is True
