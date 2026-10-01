from types import SimpleNamespace

from apps.chat import activity_stream


def test_reasoning_trace_is_visible_without_chain_of_thought(monkeypatch):
    generation = SimpleNamespace(
        id="reasoning-generation",
        state="queued",
        context_snapshot={
            "routing": {"mode": "auto", "task_taxonomy": "qa"},
            "web_search": {"used": False, "required": False},
            "web_sources": [],
            "attached_files": [],
        },
        routing_decision=SimpleNamespace(signals={"complexity_score": 0.42}),
        refresh_from_db=lambda **_kwargs: None,
    )
    monkeypatch.setattr(activity_stream, "needs_web_search", lambda _content: False)
    monkeypatch.setattr(activity_stream, "prepare", lambda **_kwargs: (generation, True))
    monkeypatch.setattr(
        activity_stream,
        "managed_run",
        lambda _generation: iter(
            [
                'event: generation\ndata: {"id":"reasoning-generation"}\n\n',
                'event: delta\ndata: {"text":"Готовый ответ"}\n\n',
                'event: completed\ndata: {"state":"completed"}\n\n',
            ]
        ),
    )

    output = "".join(
        activity_stream.managed_request_stream(
            user=SimpleNamespace(),
            conversation=SimpleNamespace(),
            idempotency_key="reasoning-trace",
            payload={"content": "Объясни бинарный поиск", "client_message_id": "msg-reasoning"},
        )
    )

    assert '"step": "reasoning"' in output
    assert "Сопоставляю контекст, источники и ограничения ответа" in output
    assert "Основания ответа проверены" in output
    assert "chain-of-thought" not in output.casefold()
    assert "скрыт" not in output.casefold()
