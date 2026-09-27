import json

from . import managed_stream


def _sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def test_provider_failure_is_recovered_without_customer_error(monkeypatch):
    generation = object()
    original_error = _sse(
        "error",
        {
            "code": "gigachat_credit_exhausted",
            "message": "Провайдер временно недоступен",
        },
    )
    recovery = [
        _sse("recovery", {"action": "emergency_fallback"}),
        _sse("delta", {"text": "Резервный ответ"}),
        _sse("completed", {"state": "completed", "cost_rub": "0.0000"}),
    ]

    monkeypatch.setattr(managed_stream, "run", lambda *_args, **_kwargs: iter([original_error]))
    monkeypatch.setattr(
        managed_stream,
        "_emergency_failover",
        lambda _generation: (True, recovery),
    )
    monkeypatch.setattr(
        managed_stream,
        "_publicize_sse_chunk",
        lambda _generation, chunk: chunk,
    )
    monkeypatch.setattr(
        managed_stream,
        "_finalize_unhandled_disconnect",
        lambda _generation: None,
    )

    chunks = list(managed_stream.managed_run(generation))

    assert chunks == recovery
    assert all("Провайдер временно недоступен" not in chunk for chunk in chunks)
    assert any("emergency_fallback" in chunk for chunk in chunks)
    assert any("Резервный ответ" in chunk for chunk in chunks)


def test_non_provider_failure_does_not_enter_emergency_failover(monkeypatch):
    generation = object()
    validation_error = _sse(
        "error",
        {"code": "cost_or_internal_error", "message": "Внутренняя ошибка"},
    )
    calls = []

    monkeypatch.setattr(managed_stream, "run", lambda *_args, **_kwargs: iter([validation_error]))
    monkeypatch.setattr(
        managed_stream,
        "_emergency_failover",
        lambda _generation: calls.append(True) or (True, []),
    )
    monkeypatch.setattr(
        managed_stream,
        "_rewrite_error_chunk_if_needed",
        lambda _generation, chunk: chunk,
    )
    monkeypatch.setattr(
        managed_stream,
        "_publicize_sse_chunk",
        lambda _generation, chunk: chunk,
    )
    monkeypatch.setattr(
        managed_stream,
        "_finalize_unhandled_disconnect",
        lambda _generation: None,
    )

    chunks = list(managed_stream.managed_run(generation))

    assert calls == []
    assert chunks == [validation_error]
