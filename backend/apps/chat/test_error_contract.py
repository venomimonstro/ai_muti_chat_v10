import json
from types import SimpleNamespace

from .error_contract import install
from .streaming import sse


def _payload(chunk):
    line = next(item for item in chunk.splitlines() if item.startswith("data:"))
    return json.loads(line[5:].strip())


def test_partial_stream_failure_never_claims_zero_charge_or_leaks_internal_cause():
    def raw_run(_generation):
        yield sse(
            "error",
            {
                "code": "timeout",
                "cause_code": "provider_timeout",
                "partial": True,
                "message": "Провайдер временно недоступен. Запрос сохранён, деньги не списаны.",
            },
        )

    module = SimpleNamespace(run=raw_run, sse=sse)
    install(module)
    chunks = list(module.run(object()))

    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")
    payload = _payload(chunks[0])
    assert payload["code"] == "partial_response_interrupted"
    assert payload["support_code"] == "partial_response_interrupted"
    assert "cause_code" not in payload
    assert payload["partial"] is True
    assert "подтверждённая стоимость" in payload["message"]
    assert "деньги не списаны" not in payload["message"]


def test_failure_before_any_text_keeps_original_error_contract():
    original = {
        "code": "timeout",
        "partial": False,
        "message": "Запрос не выполнен, деньги не списаны.",
    }

    def raw_run(_generation):
        yield sse("error", original)

    module = SimpleNamespace(run=raw_run, sse=sse)
    install(module)
    payload = _payload(next(module.run(object())))

    assert payload == original
