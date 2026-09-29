from .web_context import enrich_snapshot_with_web


def _snapshot():
    return {
        "provider_messages": [
            {"role": "system", "content": "base policy"},
            {"role": "user", "content": "hello"},
        ],
        "components": [],
        "budget": {"input_limit": 2000, "input_tokens": 0, "remaining": 2000},
    }


def test_quality_contract_stays_before_user_message(monkeypatch):
    monkeypatch.setattr("apps.chat.web_context.needs_web_search", lambda query: False)
    monkeypatch.setattr("apps.chat.web_context.live_context", lambda query: (False, "", {}))

    snapshot = enrich_snapshot_with_web(_snapshot(), "hello", required=False)

    roles = [item["role"] for item in snapshot["provider_messages"]]
    assert roles[-1] == "user"
    assert snapshot["provider_messages"][-1]["content"] == "hello"
