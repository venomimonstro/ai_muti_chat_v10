import json

import pytest

from . import managed_stream


def _sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _identity_stream_wrappers(monkeypatch):
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
    monkeypatch.setattr(
        managed_stream,
        "_finalize_unhandled_failure",
        lambda _generation: None,
    )
    monkeypatch.setattr(
        managed_stream,
        "_finalize_incomplete_stream",
        lambda _generation: None,
    )


def test_cross_model_recovery_is_forwarded_from_single_streaming_runtime(monkeypatch):
    generation = object()
    recovery = [
        _sse("recovery", {"action": "fallback", "from_model": "primary"}),
        _sse("delta", {"text": "Резервный ответ"}),
        _sse("completed", {"state": "completed", "cost_rub": "0.0100"}),
    ]
    monkeypatch.setattr(managed_stream, "run", lambda *_args, **_kwargs: iter(recovery))
    _identity_stream_wrappers(monkeypatch)

    chunks = list(managed_stream.managed_run(generation))

    assert chunks == recovery
    assert any("fallback" in chunk for chunk in chunks)
    assert any("Резервный ответ" in chunk for chunk in chunks)
    assert all("event: error" not in chunk for chunk in chunks)


def test_managed_stream_does_not_invent_a_second_failover_layer(monkeypatch):
    generation = object()
    provider_error = _sse(
        "error",
        {"code": "provider_unavailable", "message": "Временная ошибка"},
    )
    calls = []

    def one_runtime(*_args, **_kwargs):
        calls.append("run")
        return iter([provider_error])

    monkeypatch.setattr(managed_stream, "run", one_runtime)
    _identity_stream_wrappers(monkeypatch)

    chunks = list(managed_stream.managed_run(generation))

    assert calls == ["run"]
    assert chunks == [provider_error]


def test_internal_runtime_crash_is_not_reported_as_user_cancellation(monkeypatch):
    generation = object()
    calls = []

    def crashing_runtime(*_args, **_kwargs):
        yield _sse("delta", {"text": "часть"})
        raise RuntimeError("unexpected runtime crash")

    monkeypatch.setattr(managed_stream, "run", crashing_runtime)
    monkeypatch.setattr(managed_stream, "_rewrite_error_chunk_if_needed", lambda _g, chunk: chunk)
    monkeypatch.setattr(managed_stream, "_publicize_sse_chunk", lambda _g, chunk: chunk)
    monkeypatch.setattr(managed_stream, "_finalize_unhandled_disconnect", lambda _g: calls.append("cancel"))
    monkeypatch.setattr(managed_stream, "_finalize_unhandled_failure", lambda _g: calls.append("failed"))
    monkeypatch.setattr(managed_stream, "_finalize_incomplete_stream", lambda _g: calls.append("incomplete"))

    with pytest.raises(RuntimeError, match="unexpected runtime crash"):
        list(managed_stream.managed_run(generation))

    assert calls == ["failed"]


def test_consumer_close_is_the_only_unhandled_path_treated_as_disconnect(monkeypatch):
    generation = object()
    calls = []

    def runtime(*_args, **_kwargs):
        yield _sse("delta", {"text": "один"})
        yield _sse("delta", {"text": "два"})

    monkeypatch.setattr(managed_stream, "run", runtime)
    monkeypatch.setattr(managed_stream, "_rewrite_error_chunk_if_needed", lambda _g, chunk: chunk)
    monkeypatch.setattr(managed_stream, "_publicize_sse_chunk", lambda _g, chunk: chunk)
    monkeypatch.setattr(managed_stream, "_finalize_unhandled_disconnect", lambda _g: calls.append("cancel"))
    monkeypatch.setattr(managed_stream, "_finalize_unhandled_failure", lambda _g: calls.append("failed"))
    monkeypatch.setattr(managed_stream, "_finalize_incomplete_stream", lambda _g: calls.append("incomplete"))

    stream = managed_stream.managed_run(generation)
    assert "один" in next(stream)
    stream.close()

    assert calls == ["cancel"]


def test_silent_runtime_return_fails_closed_instead_of_inventing_cancel(monkeypatch):
    generation = object()
    calls = []

    monkeypatch.setattr(managed_stream, "run", lambda *_args, **_kwargs: iter([]))
    monkeypatch.setattr(managed_stream, "_rewrite_error_chunk_if_needed", lambda _g, chunk: chunk)
    monkeypatch.setattr(managed_stream, "_publicize_sse_chunk", lambda _g, chunk: chunk)
    monkeypatch.setattr(managed_stream, "_finalize_unhandled_disconnect", lambda _g: calls.append("cancel"))
    monkeypatch.setattr(managed_stream, "_finalize_unhandled_failure", lambda _g: calls.append("failed"))
    monkeypatch.setattr(managed_stream, "_finalize_incomplete_stream", lambda _g: calls.append("incomplete"))

    assert list(managed_stream.managed_run(generation)) == []
    assert calls == ["incomplete"]
