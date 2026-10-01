from types import SimpleNamespace

from apps.chat import activity_stream


def test_activity_stream_yields_before_preflight(monkeypatch):
    called = {"prepare": False}

    def fake_prepare(**_kwargs):
        called["prepare"] = True
        generation = SimpleNamespace(
            id="generation-1",
            context_snapshot={
                "routing": {"mode": "auto", "task_taxonomy": "qa"},
                "web_search": {"used": True, "required": True},
                "web_sources": [{"id": "web:1"}, {"id": "web:2"}],
                "attached_files": [],
            },
            routing_decision=SimpleNamespace(signals={"complexity_score": 0.31}),
        )
        return generation, True

    monkeypatch.setattr(activity_stream, "needs_web_search", lambda _content: True)
    monkeypatch.setattr(activity_stream, "prepare", fake_prepare)
    monkeypatch.setattr(
        activity_stream,
        "managed_run",
        lambda _generation: iter(
            [
                'event: generation\ndata: {"id":"generation-1"}\n\n',
                'event: delta\ndata: {"text":"Ответ"}\n\n',
                'event: completed\ndata: {"state":"completed"}\n\n',
            ]
        ),
    )

    stream = activity_stream.managed_request_stream(
        user=SimpleNamespace(),
        conversation=SimpleNamespace(),
        idempotency_key="activity-test",
        payload={"content": "Что произошло сегодня?", "client_message_id": "msg-1"},
    )
    first = next(stream)
    assert "event: activity" in first
    assert "Определяю тип задачи" in first
    assert called["prepare"] is False

    # Compatibility status is also emitted before any expensive preflight work.
    second = next(stream)
    assert "event: routing" in second
    assert called["prepare"] is False

    # Search intent is announced before the synchronous search begins.
    third = next(stream)
    assert "Проверяю актуальную информацию" in third
    assert called["prepare"] is False

    fourth = next(stream)
    assert "event: routing" in fourth
    assert called["prepare"] is False

    # Only after the early SSE stages does prepare/search actually execute.
    fifth = next(stream)
    assert called["prepare"] is True
    assert "Маршрут готов" in fifth


def test_activity_stream_reports_real_source_count(monkeypatch):
    generation = SimpleNamespace(
        id="generation-2",
        context_snapshot={
            "routing": {"mode": "balanced", "task_taxonomy": "research"},
            "web_search": {"used": True, "required": True},
            "web_sources": [{"id": "web:1"}, {"id": "web:2"}, {"id": "web:3"}],
            "attached_files": [],
        },
        routing_decision=SimpleNamespace(signals={"complexity_score": 0.46}),
    )
    monkeypatch.setattr(activity_stream, "needs_web_search", lambda _content: True)
    monkeypatch.setattr(activity_stream, "prepare", lambda **_kwargs: (generation, True))
    monkeypatch.setattr(
        activity_stream,
        "managed_run",
        lambda _generation: iter(['event: completed\ndata: {"state":"completed"}\n\n']),
    )
    output = "".join(
        activity_stream.managed_request_stream(
            user=SimpleNamespace(),
            conversation=SimpleNamespace(),
            idempotency_key="activity-test-2",
            payload={"content": "Найди актуальные источники", "client_message_id": "msg-2"},
        )
    )
    assert "Нашёл актуальные источники: 3" in output
    assert '"source_count": 3' in output
