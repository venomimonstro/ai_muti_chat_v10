import os
import time

from apps.ai_registry.adapters import ProviderError
from apps.ai_registry.reliability import provider_available, record_failure, record_success


DEFAULT_ATTEMPTS = int(os.getenv("DEV_PROVIDER_KEY_ATTEMPTS", "3"))


def generate_with_key_failover(*, model, messages, max_output_tokens, adapter_factory, attempts=None):
    """Retry one priced model across its healthy credential pool.

    This intentionally never changes AIModel/provider, so pricing and provider-spend
    reservations created by the caller remain valid. Provider/key health is updated
    between attempts, allowing adapter_factory(model) to select another healthy key.
    """
    maximum = max(1, min(int(attempts or DEFAULT_ATTEMPTS), 5))
    last_error = None
    attempted = 0
    for _index in range(maximum):
        if attempted and not provider_available(model.provider):
            break
        adapter = adapter_factory(model)
        started = time.monotonic()
        attempted += 1
        try:
            result = adapter.generate(
                model=model.upstream_model or model.slug,
                messages=messages,
                max_output_tokens=max_output_tokens,
            )
        except ProviderError as exc:
            last_error = exc
            record_failure(model.provider, exc, adapter=adapter)
            if not exc.retryable:
                raise
            continue
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        record_success(model.provider, latency_ms, adapter=adapter)
        return result, attempted
    if last_error is not None:
        raise last_error
    raise ProviderError(
        "Provider became unavailable before retry",
        code="provider_unavailable",
        retryable=True,
    )
